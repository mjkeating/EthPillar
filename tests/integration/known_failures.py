"""Known-failures registry loader and CI notice helpers (integration / unit).

Reads :file:`known_failures.yaml`. Used by:
  - ``check_client_versions.sh`` / ``known_version_mismatches.sh`` (version pairs)
  - ``run_docker_tests.py`` (classify FAIL logs, emit GHA annotations, step summary)
  - soft-skip / live-skip call sites via :func:`emit_ci_notice`
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

REGISTRY_PATH = Path(__file__).resolve().parent / "known_failures.yaml"
NOTICE_PREFIX = "ETHPILLAR_CI_NOTICE"


@dataclass
class KnownFailureEntry:
    """One tracked known-failure row from the registry."""

    id: str
    issue: str
    added: str
    reason: str
    match_kind: str
    pattern: str = ""
    installed: str = ""
    expected: str = ""
    cells: list[str] = field(default_factory=list)
    clients: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    # When True, a closed GitHub issue must not be reported as "fixed"
    # (issue was closed without resolving the tracked failure).
    issue_closed_unfixed: bool = False

    def applies_to_cell(self, cell: str) -> bool:
        """Return True when this entry is scoped to *cell* (name / client / network)."""
        cell_l = (cell or "").strip()
        if not cell_l:
            return False
        if self.cells and any(_cell_name_matches(cell_l, c) for c in self.cells):
            return True
        if self.match_kind == "version_mismatch":
            return any(
                _client_mentioned(cell_l, client) for client in self.clients
            )
        if self.clients or self.networks:
            client_ok = (
                not self.clients
                or any(_client_mentioned(cell_l, c) for c in self.clients)
            )
            network_ok = (
                not self.networks
                or any(_network_mentioned(cell_l, n) for n in self.networks)
            )
            return client_ok and network_ok
        return False

    def all_clear_message(self, cell: str = "") -> str:
        """Human-readable did-not-reproduce notice for GHA / step summary."""
        where = f" ({cell})" if cell else ""
        if self.match_kind == "version_mismatch":
            pair = f"installed {self.installed} vs expected {self.expected}"
            return (
                f"Known failure {self.id}{where}: tracked version pair ({pair}) "
                f"was not observed — consider removing the entry"
            )
        return (
            f"Known failure {self.id} did not reproduce{where} — "
            f"upstream may be fixed, consider removing the entry"
        )


@dataclass
class CiNotice:
    """One surfaced known-failure / soft-skip / live-skip row for summary + artifact."""

    kind: str  # known-failure | soft-skip | live-skip | all-clear
    status: str  # hit | did-not-reproduce | skipped
    id: str = ""
    cell: str = ""
    client: str = ""
    reason: str = ""
    issue: str = ""
    issue_state: str = ""  # open | closed | "" when unchecked
    link: str = ""

    def to_dict(self) -> dict[str, str]:
        return {k: v for k, v in asdict(self).items() if v}


def _strip_v(tag: str) -> str:
    return (tag or "").lstrip("vV").strip()


def _cell_name_matches(cell: str, listed: str) -> bool:
    cell_n = cell.replace(" ", "_")
    listed_n = listed.replace(" ", "_")
    return cell_n == listed_n or cell_n.endswith(f"_{listed_n}") or listed_n in cell_n


def _client_mentioned(cell: str, client: str) -> bool:
    token = (client or "").strip().lower()
    if not token:
        return False
    # Cell labels use Title-Case client names joined by hyphens.
    return token in cell.lower().replace("_", "-")


def _network_mentioned(cell: str, network: str) -> bool:
    net = (network or "").strip().upper()
    if not net:
        return False
    return net in cell.upper().replace("-", "_")


def load_registry(path: Path | str | None = None) -> list[KnownFailureEntry]:
    """Load and normalize known-failure entries from YAML.

    Raises:
        ImportError: When PyYAML is missing. Callers must not treat this as an
            empty registry — that would silently hide known failures.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised when system python lacks PyYAML
        raise ImportError(
            "PyYAML is required to load tests/integration/known_failures.yaml; "
            "use the project .venv (ETHPILLAR_PYTHON) or install python3-yaml"
        ) from exc

    registry = Path(path) if path else REGISTRY_PATH
    raw = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    entries: list[KnownFailureEntry] = []
    for item in raw.get("entries") or []:
        match = item.get("match") or {}
        kind = str(match.get("kind") or "").strip()
        entries.append(
            KnownFailureEntry(
                id=str(item.get("id") or "").strip(),
                issue=str(item.get("issue") or "").strip(),
                added=str(item.get("added") or "").strip(),
                reason=" ".join(str(item.get("reason") or "").split()),
                match_kind=kind,
                pattern=str(match.get("pattern") or ""),
                installed=_strip_v(str(match.get("installed") or "")),
                expected=_strip_v(str(match.get("expected") or "")),
                cells=[str(c) for c in (item.get("cells") or [])],
                clients=[str(c).lower() for c in (item.get("clients") or [])],
                networks=[str(n).upper() for n in (item.get("networks") or [])],
                issue_closed_unfixed=bool(item.get("issue_closed_unfixed")),
            )
        )
    return entries


def version_mismatch_is_known(
    client: str,
    installed: str,
    expected: str,
    entries: Optional[Iterable[KnownFailureEntry]] = None,
) -> Optional[KnownFailureEntry]:
    """Return the matching version_mismatch entry, or None."""
    client_l = (client or "").strip().lower()
    inst = _strip_v(installed)
    exp = _strip_v(expected)
    for entry in entries if entries is not None else load_registry():
        if entry.match_kind != "version_mismatch":
            continue
        if client_l not in entry.clients:
            continue
        if entry.installed == inst and entry.expected == exp:
            return entry
    return None


def known_upstream_version_mismatch(client: str, installed: str, expected: str) -> bool:
    """Bash-compatible bool: True when the installed/expected pair is tracked."""
    return version_mismatch_is_known(client, installed, expected) is not None


def match_log_known_failure(
    cell: str,
    log_text: str,
    entries: Optional[Iterable[KnownFailureEntry]] = None,
) -> Optional[KnownFailureEntry]:
    """Return the first log-signature entry that applies to *cell* and matches *log_text*."""
    text = log_text or ""
    for entry in entries if entries is not None else load_registry():
        if entry.match_kind not in ("log_substring", "log_regex"):
            continue
        if not entry.applies_to_cell(cell):
            continue
        if entry.match_kind == "log_substring":
            if entry.pattern and entry.pattern in text:
                return entry
        elif entry.match_kind == "log_regex":
            if entry.pattern and re.search(entry.pattern, text):
                return entry
    return None


def log_entries_for_cell(
    cell: str,
    entries: Optional[Iterable[KnownFailureEntry]] = None,
) -> list[KnownFailureEntry]:
    """Log-signature entries that apply to *cell* (for all-clear checks)."""
    out: list[KnownFailureEntry] = []
    for entry in entries if entries is not None else load_registry():
        if entry.match_kind in ("log_substring", "log_regex") and entry.applies_to_cell(
            cell
        ):
            out.append(entry)
    return out


def version_entries_for_client(
    client: str,
    entries: Optional[Iterable[KnownFailureEntry]] = None,
) -> list[KnownFailureEntry]:
    """version_mismatch entries for *client*."""
    client_l = (client or "").strip().lower()
    return [
        e
        for e in (entries if entries is not None else load_registry())
        if e.match_kind == "version_mismatch" and client_l in e.clients
    ]


def emit_ci_notice(payload: dict[str, Any]) -> None:
    """Print a machine-parseable notice line (and a GHA annotation when in Actions).

    Inside Docker exec the line is captured in the cell log; the host orchestrator
    re-emits annotations. Outside Docker (live-release job) stdout reaches GHA.
    """
    line = f"{NOTICE_PREFIX} {json.dumps(payload, sort_keys=True)}"
    print(line, flush=True)
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true":
        _emit_gha_annotation(payload)


def _emit_gha_annotation(payload: dict[str, Any]) -> None:
    kind = str(payload.get("kind") or "")
    status = str(payload.get("status") or "")
    entry_id = str(payload.get("id") or payload.get("client") or "notice")
    issue = str(payload.get("issue") or payload.get("link") or "")
    reason = str(payload.get("reason") or "")
    cell = str(payload.get("cell") or "")
    where = f" ({cell})" if cell else ""

    if kind == "known-failure" and status == "hit":
        msg = f"Known failure {entry_id}{where}: {reason}"
        if issue:
            msg += f" — {issue}"
        print(f"::warning::{msg}", flush=True)
    elif kind in ("all-clear", "known-failure") and status == "did-not-reproduce":
        msg = str(payload.get("clear_message") or "").strip()
        # notice_all_clear stores the full wording in reason when re-emitted
        # from CiNotice.to_dict() (which drops clear_message / match_kind).
        if not msg:
            reason_msg = str(payload.get("reason") or "").strip()
            if reason_msg.startswith("Known failure"):
                msg = reason_msg
        if not msg:
            match_kind = str(payload.get("match_kind") or "")
            if match_kind == "version_mismatch":
                installed = str(payload.get("installed") or "")
                expected = str(payload.get("expected") or "")
                pair = (
                    f"installed {installed} vs expected {expected}"
                    if installed or expected
                    else "installed/expected"
                )
                msg = (
                    f"Known failure {entry_id}{where}: tracked version pair "
                    f"({pair}) was not observed — consider removing the entry"
                )
            else:
                msg = (
                    f"Known failure {entry_id} did not reproduce{where} — "
                    f"upstream may be fixed, consider removing the entry"
                )
        if issue and issue not in msg:
            msg += f" ({issue})"
        issue_state = str(payload.get("issue_state") or "")
        # Never append "issue is closed" when the entry opted out (closed unfixed).
        if issue_state == "closed" and not payload.get("issue_closed_unfixed"):
            msg += " [linked GitHub issue is closed]"
        print(f"::notice::{msg}", flush=True)
    elif kind in ("soft-skip", "live-skip") and status == "skipped":
        client = str(payload.get("client") or entry_id)
        msg = f"Soft-skip {client}{where}: {reason}"
        print(f"::warning::{msg}", flush=True)


def parse_notice_lines(text: str) -> list[CiNotice]:
    """Parse ``ETHPILLAR_CI_NOTICE`` JSON lines from a log blob."""
    notices: list[CiNotice] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith(NOTICE_PREFIX):
            continue
        blob = line[len(NOTICE_PREFIX) :].strip()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        notices.append(
            CiNotice(
                kind=str(data.get("kind") or ""),
                status=str(data.get("status") or ""),
                id=str(data.get("id") or ""),
                cell=str(data.get("cell") or ""),
                client=str(data.get("client") or ""),
                reason=str(data.get("reason") or ""),
                issue=str(data.get("issue") or ""),
                issue_state=str(data.get("issue_state") or ""),
                link=str(data.get("link") or data.get("issue") or ""),
            )
        )
    return notices


def maybe_github_issue_state(issue_url: str, timeout: float = 3.0) -> str:
    """Return ``open`` / ``closed`` for a GitHub issue URL, or ``\"\"`` on any failure.

    Uses the Issues API when ``GITHUB_TOKEN`` is set. Skips quietly on errors /
    rate limits so Nightly never turns red over triage metadata.
    """
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)",
        (issue_url or "").strip(),
    )
    if not match:
        return ""
    owner, repo, number = match.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ethpillar-known-failures",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return ""
    state = str(payload.get("state") or "").lower()
    return state if state in ("open", "closed") else ""


def issue_state_for_entry(
    entry: KnownFailureEntry, timeout: float = 3.0
) -> str:
    """Return issue state for *entry*, or ``\"\"`` when state must be ignored.

    Entries with ``issue_closed_unfixed`` skip the API check so a closed-without-
    fix issue is never surfaced as evidence the failure is gone.
    """
    if entry.issue_closed_unfixed or not entry.issue:
        return ""
    return maybe_github_issue_state(entry.issue, timeout=timeout)


def all_clear_notice_payload(
    entry: KnownFailureEntry,
    *,
    cell: str = "",
    client: str = "",
    issue_state: str | None = None,
) -> dict[str, Any]:
    """Build the CI-notice payload for a did-not-reproduce / all-clear event."""
    if issue_state is None:
        issue_state = issue_state_for_entry(entry)
    if entry.issue_closed_unfixed:
        issue_state = ""
    return {
        "kind": "all-clear",
        "status": "did-not-reproduce",
        "id": entry.id,
        "cell": cell,
        "client": client or (entry.clients[0] if entry.clients else ""),
        "issue": entry.issue,
        "issue_state": issue_state or "",
        "issue_closed_unfixed": entry.issue_closed_unfixed,
        "match_kind": entry.match_kind,
        "installed": entry.installed,
        "expected": entry.expected,
        "reason": entry.reason,
        "clear_message": entry.all_clear_message(cell),
        "link": entry.issue,
    }


def classify_failed_cell(
    cell: str,
    log_text: str,
    entries: Optional[Iterable[KnownFailureEntry]] = None,
) -> tuple[str, Optional[KnownFailureEntry]]:
    """Classify a FAIL cell as ``KNOWN_FAIL`` or ``FAIL``.

    Returns ``(status, entry_or_None)``.
    """
    entry = match_log_known_failure(cell, log_text, entries=entries)
    if entry is not None:
        return "KNOWN_FAIL", entry
    return "FAIL", None


def write_ci_notices_artifact(notices: list[CiNotice], dest: Path | str) -> Path:
    """Write machine-readable ``ci-notices.json`` for triage jobs."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "notices": [n.to_dict() for n in notices],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def append_step_summary(notices: list[CiNotice], summary_path: str | None = None) -> None:
    """Append a markdown table to ``$GITHUB_STEP_SUMMARY`` when set."""
    path = summary_path or os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## Known failures & skips",
        "",
        "| Entry / skip | Cell | Status | Link |",
        "| --- | --- | --- | --- |",
    ]
    if not notices:
        lines.append("| — | — | none | — |")
    else:
        for n in notices:
            label = n.id or n.client or n.kind
            cell = n.cell or "—"
            status = n.status or "—"
            link = n.link or n.issue or ""
            link_cell = f"[issue]({link})" if link else "—"
            if n.issue_state:
                status = f"{status} (issue {n.issue_state})"
            reason = (n.reason or "").replace("|", "\\|")
            if reason:
                label = f"{label}: {reason}" if n.kind in ("soft-skip", "live-skip") else label
            lines.append(f"| {label} | {cell} | {status} | {link_cell} |")
    lines.append("")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def notice_from_known_hit(
    entry: KnownFailureEntry,
    *,
    cell: str = "",
    client: str = "",
) -> CiNotice:
    """Build a ``hit`` notice for a matched known-failure entry."""
    return CiNotice(
        kind="known-failure",
        status="hit",
        id=entry.id,
        cell=cell,
        client=client or (entry.clients[0] if entry.clients else ""),
        reason=entry.reason,
        issue=entry.issue,
        link=entry.issue,
    )


def notice_all_clear(
    entry: KnownFailureEntry,
    *,
    cell: str = "",
    client: str = "",
    issue_state: str | None = None,
) -> CiNotice:
    """Build a did-not-reproduce notice for a registry entry."""
    payload = all_clear_notice_payload(
        entry, cell=cell, client=client, issue_state=issue_state
    )
    return CiNotice(
        kind="all-clear",
        status="did-not-reproduce",
        id=entry.id,
        cell=cell,
        client=str(payload.get("client") or ""),
        # Prefer the pair-not-observed / did-not-reproduce wording over the
        # static registry reason so summaries do not imply an upstream fix
        # for issue_closed_unfixed version mismatches.
        reason=str(payload.get("clear_message") or entry.reason),
        issue=entry.issue,
        issue_state=str(payload.get("issue_state") or ""),
        link=entry.issue,
    )


def emit_soft_skip(client: str, reason: str, cell: str = "") -> None:
    """Surface an upgrade / seed soft-skip as a CI notice + warning annotation."""
    emit_ci_notice(
        {
            "kind": "soft-skip",
            "status": "skipped",
            "client": (client or "").strip().lower() or "unknown",
            "cell": cell,
            "reason": reason,
        }
    )


def emit_live_skip(client: str, reason: str) -> None:
    """Surface a live-release pytest skip as a CI notice + warning annotation."""
    emit_ci_notice(
        {
            "kind": "live-skip",
            "status": "skipped",
            "client": (client or "").strip().lower() or "unknown",
            "reason": reason,
        }
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: ``version-mismatch CLIENT INSTALLED EXPECTED`` → exit 0 if known."""
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage: known_failures.py version-mismatch CLIENT INSTALLED EXPECTED\n"
            "       known_failures.py list",
            file=sys.stderr,
        )
        return 2
    cmd = args[0]
    if cmd == "list":
        for entry in load_registry():
            print(f"{entry.id}\t{entry.match_kind}\t{entry.issue}")
        return 0
    if cmd == "version-mismatch" and len(args) == 4:
        return 0 if known_upstream_version_mismatch(args[1], args[2], args[3]) else 1
    print(f"Unknown command: {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
