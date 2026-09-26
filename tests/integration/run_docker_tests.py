"""Integration test orchestrator for the EthPillar Docker/systemd matrix.

Builds the test image, warms checkpoint and binary caches, runs each case in an
isolated privileged container, streams live Rich UI output, and writes HTML reports
under ``tests/integration/results/run_<timestamp>/``.
"""
import os
import sys
import time
import asyncio
import subprocess
import argparse
import shlex
import re
from datetime import datetime
from typing import List, Optional, Tuple

try:
    from rich.live import Live
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
except ImportError:
    Live = Console = Group = Panel = Text = Table = None

RUN_TEST = "bash /ethpillar/tests/integration/run_test.sh"

_INTEGRATION_DIR = os.path.dirname(os.path.abspath(__file__))
if _INTEGRATION_DIR not in sys.path:
    sys.path.insert(0, _INTEGRATION_DIR)
from checkpoint_cache_common import (  # noqa: E402
    CONTAINER_CACHE_PATH,
    ensure_cache_root,
    get_cache_root,
    get_manifest_path,
)
from port_bindings import cl_supports_rpc_expose, el_supports_rpc_expose  # noqa: E402

# Keep in sync with deploy.orchestrator.PREDEFINED_COMBOS (avoid importing deploy on the host).
PREDEFINED_COMBOS = {
    "Nimbus-Nethermind": ("Nethermind", "Nimbus"),
    "Lodestar-Besu": ("Besu", "Lodestar"),
    "Teku-Besu": ("Besu", "Teku"),
    "Lighthouse-Reth": ("Reth", "Lighthouse"),
    "Caplin-Erigon": ("Erigon", "Caplin"),
}

# Matrices
combos = [
    "Caplin-Erigon",
    "Lighthouse-Reth",
    "Lodestar-Besu",
    "Nimbus-Nethermind",
    "Teku-Besu",
]

variations = [
    "--network HOODI --mev --config 'Solo Staking Node'",
    "--network SEPOLIA --config 'Full Node Only'",
]

custom_tests = [
    ("Geth-Lighthouse-Custom-Setup-SEPOLIA", f"{RUN_TEST} deploy/deploy-node.py --ec Geth --cc Lighthouse --vc Lighthouse --network SEPOLIA --mev --config 'Custom Setup'"),
    ("Nethermind-Grandine-Custom-Setup-SEPOLIA", f"{RUN_TEST} deploy/deploy-node.py --ec Nethermind --cc Grandine --vc Lighthouse --network SEPOLIA --mev --config 'Custom Setup'"),
    ("Prysm-Reth-Custom-Setup-SEPOLIA", f"{RUN_TEST} deploy/deploy-node.py --ec Reth --cc Prysm --vc Prysm --network SEPOLIA --mev --config 'Custom Setup'"),
    ("Ethrex-Teku-Custom-Setup-SEPOLIA", f"{RUN_TEST} deploy/deploy-node.py --ec Ethrex --cc Teku --network SEPOLIA --config 'Custom Setup'"),
    ("Teku-VC-Only-HOODI", f"{RUN_TEST} deploy/deploy-node.py --combo Teku-Besu --network HOODI --config 'Validator Client Only' --vc_only_bn_address http://192.168.1.123:5052"),
    ("Geth-Lighthouse-Charon-Lodestar-SEPOLIA", f"{RUN_TEST} deploy/deploy-node.py --ec Geth --cc Lighthouse --vc Lodestar --network SEPOLIA --mev --charon --config 'Custom Setup'"),
]

# Upgrade lane: deploy seeds RC-if-newer-than-LATEST else previous stable
# (see latest_override.py / find_upgrade_seed). First ethpillar upgrade must
# move to official LATEST; second must skip. No sane seed → soft-skip.
upgrade_tests = [
    ("Upgrade-Reth-Lighthouse", f"{RUN_TEST} deploy/deploy-node.py --ec Reth --cc Lighthouse --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Besu-Teku", f"{RUN_TEST} deploy/deploy-node.py --ec Besu --cc Teku --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Nethermind-Nimbus", f"{RUN_TEST} deploy/deploy-node.py --ec Nethermind --cc Nimbus --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Erigon-Caplin", f"{RUN_TEST} deploy/deploy-node.py --ec Erigon --cc Caplin --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Geth-Lodestar", f"{RUN_TEST} deploy/deploy-node.py --ec Geth --cc Lodestar --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Ethrex-Lighthouse", f"{RUN_TEST} deploy/deploy-node.py --ec Ethrex --cc Lighthouse --network SEPOLIA --config 'Full Node Only' --test-updates"),
]

switch_tests = [
    ("Switch-Reth-Lighthouse-to-Besu-Nimbus", f"{RUN_TEST} deploy/deploy-node.py --ec Reth --cc Lighthouse --network SEPOLIA --config 'Full Node Only' --test-switching"),
]

# Post-install ePBS migration cases (empty-wallet VC start).
epbs_tests = [
    (
        "Prysm-Reth-ePBS-Migration-SEPOLIA",
        f"{RUN_TEST} deploy/deploy-node.py --ec Reth --cc Prysm --vc Prysm --network SEPOLIA --mev --config 'Custom Setup' --test-epbs",
    ),
    (
        "Lodestar-Reth-ePBS-Migration-SEPOLIA",
        f"{RUN_TEST} deploy/deploy-node.py --ec Reth --cc Lodestar --vc Lodestar --network SEPOLIA "
        "--mev --config 'Custom Setup' --test-epbs",
    ),
]

def parse_clients_from_cmd(cmd: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract execution and consensus client names from a deploy command."""
    ec_match = re.search(r"--ec\s+(\S+)", cmd)
    cc_match = re.search(r"--cc\s+(\S+)", cmd)
    combo_match = re.search(r'--combo\s+"([^"]+)"', cmd) or re.search(r"--combo\s+(\S+)", cmd)
    ec = ec_match.group(1) if ec_match else None
    cc = cc_match.group(1) if cc_match else None
    if combo_match:
        combo_ec, combo_cc = PREDEFINED_COMBOS.get(combo_match.group(1), (None, None))
        ec = ec or combo_ec
        cc = cc or combo_cc
    return ec, cc


def assign_rpc_exposure_flags(tasks: List["TestTask"]) -> None:
    """Schedule the slow RPC expose/revoke cycle once per supported EL/CL client."""
    el_seen: set[str] = set()
    cl_seen: set[str] = set()
    for task in tasks:
        ec, cc = parse_clients_from_cmd(task.cmd)
        flags: List[str] = []
        if ec and el_supports_rpc_expose(ec) and ec not in el_seen:
            flags.append("--rpc-exposure-el")
            el_seen.add(ec)
        if cc and cl_supports_rpc_expose(cc) and cc not in cl_seen:
            flags.append("--rpc-exposure-cl")
            cl_seen.add(cc)
        if flags:
            task.cmd = f"{task.cmd} {' '.join(flags)}"

    missing_el = {
        name for name in ("Nethermind", "Besu", "Erigon", "Geth", "Reth", "Ethrex")
        if el_supports_rpc_expose(name)
    } - el_seen
    missing_cl = {
        name for name in ("Nimbus", "Lodestar", "Lighthouse", "Grandine", "Prysm", "Teku")
        if cl_supports_rpc_expose(name)
    } - cl_seen
    if missing_el or missing_cl:
        print(
            f"WARNING: RPC exposure not scheduled for EL={sorted(missing_el)} CL={sorted(missing_cl)}",
            flush=True,
        )
    else:
        exposure_cases = sum(
            "--rpc-exposure-el" in task.cmd or "--rpc-exposure-cl" in task.cmd
            for task in tasks
        )
        print(
            f"RPC exposure scheduled for {len(el_seen)} EL and {len(cl_seen)} CL clients "
            f"({exposure_cases} test cases)",
            flush=True,
        )

_FAIL_LINE_RE = re.compile(
    r"^(?:❌\s*|FAIL[:\s]\s*)(.+)$",
    re.IGNORECASE,
)


def extract_failure_reason(log_file: Optional[str], max_reasons: int = 3) -> str:
    """Pull concise failure lines from a test log for CI-friendly summaries.

    Prefers explicit markers used by the integration scripts (``❌ …``, ``FAIL: …``).
    Falls back to the last non-empty log line when no marker is present.
    """
    if not log_file or not os.path.exists(log_file):
        return "no log available"

    reasons: List[str] = []
    seen: set[str] = set()
    last_line = ""
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                last_line = line
                if line.startswith("Failed to start container"):
                    candidate = line
                else:
                    match = _FAIL_LINE_RE.match(line)
                    if not match:
                        continue
                    candidate = match.group(1).strip()
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    reasons.append(candidate)
    except OSError:
        return "could not read log"

    if reasons:
        return "; ".join(reasons[-max_reasons:])
    if last_line:
        return last_line[:200]
    return "see log for details"


class TestTask:
    """Mutable state for one integration case (container, log path, status, timing)."""

    def __init__(self, label, cmd, display_var, log_suffix=""):
        self.label = label
        self.cmd = cmd
        self.display_var = display_var
        self.log_suffix = log_suffix
        
        # Derive a safe file name
        base_name = "".join([c if c.isalnum() or c == '-' else '_' for c in label])
        import re
        base_name = re.sub(r'_+', '_', base_name)
        if log_suffix:
            base_name = f"{base_name}_{log_suffix}"
            
        self.log_name = base_name
        self.log_file = None
        self.container_name = f"ep-test-{base_name[:60]}"
        self.status = "PENDING"
        self.duration = 0
        self.start_time = 0
        self.failure_reason: Optional[str] = None

def generate_tests():
    """Build the full integration matrix as :class:`TestTask` instances."""
    tests = []
    import re
    for combo in combos:
        for var in variations:
            actual_var = var
                
            match = re.search(r'--network\s+(\S+)', actual_var)
            local_network = match.group(1) if match else ""

            cmd = f"{RUN_TEST} deploy/deploy-node.py --combo \"{combo}\" {actual_var}"
            tests.append(TestTask(combo, cmd, actual_var, local_network))

    for label, cmd in custom_tests:
        tests.append(TestTask(label, cmd, "Custom"))
    for label, cmd in upgrade_tests:
        tests.append(TestTask(label, cmd, "Upgrade"))
    for label, cmd in switch_tests:
        tests.append(TestTask(label, cmd, "Switch"))
    for label, cmd in epbs_tests:
        tests.append(TestTask(label, cmd, "ePBS"))

    return tests

def tail_file(filepath, lines=20):
    """Return the last *lines* of *filepath* for live log panels."""
    if not filepath or not os.path.exists(filepath):
        return "Waiting for logs..."
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.readlines()
            return "".join(content[-lines:]).strip()
    except Exception as e:
        return f"Error reading log: {e}"

async def run_test(task: TestTask, results_dir: str, semaphore: asyncio.Semaphore):
    """Start one systemd container, exec the test command, and record PASS/FAIL."""
    async with semaphore:
        task.status = "RUNNING"
        task.log_file = os.path.join(results_dir, f"{task.log_name}.log")
        
        # Ensure log file exists so we can tail it immediately
        with open(task.log_file, "w") as f:
            f.write(f"Starting test {task.label}...\n")
            
        task.start_time = time.time()
        
        try:
            # Cleanup any orphaned container
            subprocess.run(f"docker rm -f {task.container_name}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            flags = "--privileged --cgroupns=host --tmpfs /run --tmpfs /run/lock"
            cwd = os.getcwd()
            token = os.environ.get("GITHUB_TOKEN", "")
            token_flag = f"-e GITHUB_TOKEN={shlex.quote(token)}" if token else ""
            cache_mount = ""
            cache_path = get_cache_root()
            if os.path.isfile(get_manifest_path()):
                cache_mount = (
                    f"-v {shlex.quote(cache_path)}:{CONTAINER_CACHE_PATH}:ro "
                )
            run_cmd = (
                f"docker run -d --name {task.container_name} {flags} {token_flag} "
                f"{integration_container_env_flags()}"
                f"{cache_mount}"
                f"-v {shlex.quote(cwd)}:/ethpillar ethpillar-rebuild"
            )
            
            proc = await asyncio.create_subprocess_shell(run_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            
            if proc.returncode != 0:
                task.status = "FAIL"
                task.duration = int(time.time() - task.start_time)
                with open(task.log_file, "a") as f:
                    f.write(f"\nFailed to start container. Exit code {proc.returncode}\n")
                task.failure_reason = extract_failure_reason(task.log_file)
                return
                
            await asyncio.sleep(3) # Wait for systemd to initialize
            
            # Exec the actual test
            exec_env = "-e PYTHONUNBUFFERED=1"
            if token:
                exec_env += f" -e GITHUB_TOKEN={shlex.quote(token)}"
            exec_cmd = f"docker exec {exec_env} {task.container_name} {task.cmd} >> {task.log_file} 2>&1"
            proc = await asyncio.create_subprocess_shell(exec_cmd)
            await proc.wait()
            
            task.status = "PASS" if proc.returncode == 0 else "FAIL"
            task.duration = int(time.time() - task.start_time)
            if task.status == "FAIL":
                task.failure_reason = extract_failure_reason(task.log_file)
        finally:
            subprocess.run(f"docker rm -f {task.container_name}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def headless_monitor(tasks: list[TestTask], exec_coros):
    """Run tests in CI/headless mode with simple progress lines instead of Rich UI."""
    completed = set()

    async def monitor():
        while True:
            for task in tasks:
                if task.status in ("PASS", "FAIL") and task.log_name not in completed:
                    print(f"[{task.status}] {task.log_name} ({task.duration}s)", flush=True)
                    if task.status == "FAIL" and task.failure_reason:
                        print(f"       {task.failure_reason}", flush=True)
                    completed.add(task.log_name)
            if all(task.status in ("PASS", "FAIL") for task in tasks):
                break
            await asyncio.sleep(5)

    await asyncio.gather(monitor(), *exec_coros)


def use_rich_ui() -> bool:
    """Rich alternate-screen UI needs a TTY; GitHub Actions sets CI=true without one."""
    if os.environ.get("CI") == "true":
        return False
    if Console is None:
        return False
    return sys.stdout.isatty()


async def ui_loop(tasks: list[TestTask]):
    """Render the Rich live dashboard until every task completes."""
    if Console is None:
        print("The 'rich' library is required. Install it using: pip install rich")
        sys.exit(1)
    console = Console()
    with Live(refresh_per_second=2, console=console, screen=True, transient=False) as live:
        # Build the completed table ONCE with all rows pre-populated.
        # screen=True uses the alternate terminal buffer — no flicker.
        status_lookup = {}
        dur_lookup = {}
        var_lookup = {}
        for t in tasks:
            var_lookup[t.log_name] = t.display_var
            status_lookup[t.log_name] = "[dim]PENDING[/dim]"
            dur_lookup[t.log_name] = "—"

        def build_table():
            table = Table(title="Completed Tests", expand=True, show_lines=False)
            table.add_column("Test", style="cyan", no_wrap=True)
            table.add_column("Variation", style="magenta", no_wrap=True)
            table.add_column("Status", justify="center", no_wrap=True)
            table.add_column("Duration", justify="right", no_wrap=True)
            for t in tasks:
                table.add_row(t.label, var_lookup[t.log_name], status_lookup[t.log_name], dur_lookup[t.log_name])
            return table

        while True:
            running_tasks = [t for t in tasks if t.status == "RUNNING"]
            pending_tasks = [t for t in tasks if t.status == "PENDING"]

            for t in tasks:
                if t.status == "PASS":
                    status_lookup[t.log_name] = "[green]PASS[/green]"
                    dur_lookup[t.log_name] = f"{t.duration}s"
                elif t.status == "FAIL":
                    status_lookup[t.log_name] = "[red]FAIL[/red]"
                    dur_lookup[t.log_name] = f"{t.duration}s"
                elif t.status == "RUNNING":
                    status_lookup[t.log_name] = "[yellow]RUNNING[/yellow]"
                    dur_lookup[t.log_name] = f"{int(time.time() - t.start_time)}s"

            renderables = [build_table()]

            for t in running_tasks:
                table_height = len(tasks) + 5
                available = max(6, console.size.height - table_height - 2)
                panel_height = max(6, available // len(running_tasks))
                log_text = tail_file(t.log_file, max(5, panel_height - 4))
                dur = int(time.time() - t.start_time)
                panel = Panel(
                    Text.from_ansi(log_text),
                    title=f"[yellow]RUNNING: {t.label} ({dur}s)[/yellow]",
                    border_style="yellow",
                    height=panel_height
                )
                renderables.append(panel)

            if not running_tasks and not pending_tasks:
                renderables.append(Text("All tests complete.", style="green bold"))

            live.update(Group(*renderables))

            if not running_tasks and not pending_tasks:
                break
            await asyncio.sleep(0.5)

def get_git_commit():
    """Return short git SHA for the current checkout, or ``unknown``."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"

def generate_html_report(tasks, results_dir, total_duration, timestamp, commit):
    """Write ``index.html`` summarizing pass/fail, durations, and log links."""
    html_path = os.path.join(results_dir, "index.html")
    passed = len([t for t in tasks if t.status == "PASS"])
    failed = len([t for t in tasks if t.status == "FAIL"])
    skipped = len([t for t in tasks if t.status == "SKIPPED"])
    overall_result = "PASS" if failed == 0 else "FAIL"
    overall_class = "status-pass" if overall_result == "PASS" else "status-fail"
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Integration Test Report - {timestamp}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; background: #f8f9fa; color: #333; }}
        .container {{ max-width: 1200px; margin: auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        h1 {{ color: #007bff; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .summary {{ margin-bottom: 20px; font-weight: bold; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ border: 1px solid #dee2e6; padding: 12px; text-align: left; }}
        th {{ background-color: #f1f3f5; color: #495057; }}
        tr:hover {{ background-color: #f8f9fa; }}
        .status-pass {{ color: #28a745; font-weight: bold; }}
        .status-fail {{ color: #dc3545; font-weight: bold; }}
        .status-skipped {{ color: #ffc107; font-weight: bold; }}
        a {{ color: #007bff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .footer {{ margin-top: 30px; font-size: 0.9em; color: #6c757d; text-align: center; }}
        @media (prefers-color-scheme: dark) {{
            body {{ background: #121212; color: #e0e0e0; }}
            .container {{ background: #1e1e1e; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }}
            h1 {{ color: #4dabf7; border-bottom: 2px solid #333; }}
            th {{ background-color: #2c2c2c; color: #e0e0e0; border: 1px solid #444; }}
            td {{ border: 1px solid #444; }}
            tr:hover {{ background-color: #2a2a2a; }}
            .status-pass {{ color: #4cd964; }}
            .status-fail {{ color: #ff3b30; }}
            .status-skipped {{ color: #ffcc00; }}
            a {{ color: #4dabf7; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Integration Test Report</h1>
        <div class="summary">
            Overall Result: <span class="{overall_class}">{overall_result}</span> <br>
            Run Time: {timestamp} <br>
            Commit: <code>{commit}</code> <br>
            Total Duration: {total_duration}s <br>
            Total Tests: {len(tasks)} <br>
            Passed: {passed} <br>
            Failed: {failed} <br>
            Skipped: {skipped}
        </div>
        <table>
            <thead>
                <tr>
                    <th>Script</th>
                    <th>Variation</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Error</th>
                    <th>Log</th>
                </tr>
            </thead>
            <tbody>
"""
    for t in tasks:
        status_class = f"status-{t.status.lower()}"
        log_link = f"<a href='{os.path.basename(t.log_file)}' target='_blank'>View Log</a>" if t.log_file else "-"
        error_cell = ""
        if t.status == "FAIL":
            error_cell = t.failure_reason or extract_failure_reason(t.log_file)
            # Escape minimal HTML entities for the report table.
            error_cell = (
                error_cell.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
        html += (
            f"                <tr><td>{t.label}</td><td>{t.display_var}</td>"
            f"<td class='{status_class}'>{t.status}</td><td>{t.duration}s</td>"
            f"<td>{error_cell or '—'}</td><td>{log_link}</td></tr>\n"
        )
        
    html += """            </tbody>
        </table>
        <div class="footer">
            EthPillar Integration Suite
        </div>
    </div>
</body>
</html>
"""
    with open(html_path, "w") as f:
        f.write(html)
    return html_path

def _host_integration_ids() -> tuple[int, int]:
    """Return (uid, gid) for the bind-mounted repo owner inside containers."""
    try:
        return os.getuid(), os.getgid()
    except AttributeError:
        return 1000, 1000


def integration_container_env_flags() -> str:
    """Docker -e flags so the in-container test user matches the host checkout owner."""
    uid, gid = _host_integration_ids()
    return (
        f"-e ETHPILLAR_INTEGRATION_UID={uid} "
        f"-e ETHPILLAR_INTEGRATION_GID={gid} "
        f"-e ETHPILLAR_INTEGRATION_USER=epstaker "
    )


def _docker_repo_mount(cwd: str) -> str:
    return (
        f"-v {shlex.quote(cwd)}:/ethpillar "
        f"-e PYTHONPATH=/ethpillar/tests/integration "
        f"{integration_container_env_flags()}"
    )


def docker_reset_binary_cache_access_log(cwd: str) -> None:
    """Clear the access log inside Docker before the matrix (runs as root).

    Cache housekeeping may need to remove root-owned files left from prior runs;
    deploy/update tests themselves still run as the integration user.
    """
    cmd = (
        f"docker run --rm {_docker_repo_mount(cwd)} ethpillar-rebuild "
        "python3 -c \"from binary_cache_common import prepare_binary_cache_dir; prepare_binary_cache_dir()\""
    )
    subprocess.run(cmd, shell=True, check=True)


def docker_prune_binary_cache(cwd: str) -> None:
    """Prune stale cache entries inside Docker after the matrix (runs as root)."""
    cmd = (
        f"docker run --rm {_docker_repo_mount(cwd)} ethpillar-rebuild "
        "python3 -c \"from binary_cache_common import prune_unaccessed_binary_cache; "
        "prune_unaccessed_binary_cache()\""
    )
    subprocess.run(cmd, shell=True, check=False)


def fix_cache_permissions_for_ci() -> None:
    """Broaden read access on host cache dirs after Docker integration tests.

    Cache files are usually owned by the integration user (host-matched UID) with
    mode 644 from sitecustomize/extract_cache. When anything still lands as root
    (e.g. legacy paths), ``sudo chmod -R a+rX`` lets the CI runner tar caches.
    """
    if os.environ.get("CI") != "true":
        return
    for name in ("cache", "checkpoint_cache"):
        path = os.path.join(os.getcwd(), "tests", "integration", name)
        if os.path.isdir(path):
            subprocess.run(["sudo", "chmod", "-R", "a+rX", path], check=False)


async def main():
    """Entry point: build image, warm caches, run matrix, emit report, set exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel", type=int, default=1, help="Max concurrent tests (must be 1)")
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        help="Only run tests whose label/log name contains this substring (repeatable)",
    )
    args = parser.parse_args()
    if args.parallel != 1:
        print("Concurrency > 1 is not yet supported with the Rich UI. Defaulting to 1.")
        args.parallel = 1
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    results_dir = os.path.join(os.getcwd(), "tests", "integration", "results", f"run_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)

    if not os.environ.get("GITHUB_TOKEN"):
        print(
            "WARNING: GITHUB_TOKEN is not set. Integration tests call the GitHub API "
            "for every client install; without a token you may hit rate limits (403)."
        )
        print(
            "         Set a read-only classic PAT first, e.g. PowerShell:\n"
            "           $env:GITHUB_TOKEN = \"ghp_...\""
        )
    else:
        print("GITHUB_TOKEN is set — GitHub API calls will be authenticated.")

    cwd = os.getcwd()
    binary_cache_dir = os.path.join(cwd, "tests", "integration", "cache")
    os.makedirs(binary_cache_dir, exist_ok=True)

    print("Rebuilding Docker image...")
    res = subprocess.run("docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .", shell=True)
    if res.returncode != 0:
        print("Failed to build Docker image.")
        sys.exit(1)

    docker_reset_binary_cache_access_log(cwd)
    print(f"Binary cache access log reset ({binary_cache_dir})")

    cache_dir = ensure_cache_root()
    print(f"Warming checkpoint cache at {cache_dir} ...")
    warm_cmd = (
        f"docker run --rm -v {shlex.quote(cwd)}:/ethpillar "
        f"-v {shlex.quote(cache_dir)}:{CONTAINER_CACHE_PATH} "
        f"-e ETHPILLAR_CHECKPOINT_CACHE_DIR={CONTAINER_CACHE_PATH} "
        f"ethpillar-rebuild python3 /ethpillar/tests/integration/warm_checkpoint_cache.py"
    )
    warm_res = subprocess.run(warm_cmd, shell=True)
    if warm_res.returncode != 0:
        print("WARNING: Checkpoint cache warm failed; tests will use upstream checkpoint URLs.")

    tasks = generate_tests()
    if args.filter:
        needles = [f.lower() for f in args.filter]
        tasks = [
            t for t in tasks
            if any(n in t.label.lower() or n in t.log_name.lower() for n in needles)
        ]
        if not tasks:
            print(f"No tests matched --filter {args.filter!r}")
            sys.exit(1)
        print(f"Filtered to {len(tasks)} test(s): {[t.log_name for t in tasks]}")
    assign_rpc_exposure_flags(tasks)
    semaphore = asyncio.Semaphore(args.parallel)
    
    start_time = time.time()
    
    exec_coros = [run_test(t, results_dir, semaphore) for t in tasks]

    if use_rich_ui():
        await asyncio.gather(ui_loop(tasks), *exec_coros)
    else:
        print(f"Running {len(tasks)} integration tests (headless)...")
        await headless_monitor(tasks, exec_coros)
    
    total_duration = int(time.time() - start_time)

    docker_prune_binary_cache(cwd)
    fix_cache_permissions_for_ci()
    
    html_path = generate_html_report(tasks, results_dir, total_duration, timestamp, get_git_commit())
    print(f"\n✅ Report generated: {html_path}")
    print(f"⏱️ Total runtime: {total_duration}s")
    
    failed = [t for t in tasks if t.status == "FAIL"]
    if failed:
        print(f"❌ {len(failed)} tests failed.")
        for task in failed:
            reason = task.failure_reason or extract_failure_reason(task.log_file)
            print(f"  • {task.log_name}: {reason}")
        sys.exit(1)
    else:
        print("✅ All integration tests passed!")
        sys.exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTests aborted by user.")
        sys.exit(1)
