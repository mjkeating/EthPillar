#!/usr/bin/env bash
# Assert that install.sh produced a working EthPillar tree at --expected-repo.
set -euo pipefail

EXPECTED_REPO=""
FORBID_REPO=""

usage() {
  cat <<'EOF'
Usage: verify_install.sh --expected-repo PATH [--forbid-repo PATH]

Checks:
  - ethpillar.sh exists and is executable in the expected repo
  - /usr/local/bin/ethpillar symlink points at that script
  - `ethpillar` is on PATH
  - Python venv exists with runtime dependencies
  - functions.sh resolves BASE_DIR to the expected repo
  - optional: symlink must not target --forbid-repo
EOF
}

die() {
  echo "FAIL: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --expected-repo)
      EXPECTED_REPO="$2"
      shift 2
      ;;
    --forbid-repo)
      FORBID_REPO="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$EXPECTED_REPO" ]] || die "--expected-repo is required"

EXPECTED_REPO="$(readlink -f "$EXPECTED_REPO")"
EXPECTED_SCRIPT="${EXPECTED_REPO}/ethpillar.sh"

[[ -f "$EXPECTED_SCRIPT" ]] || die "missing ${EXPECTED_SCRIPT}"
[[ -x "$EXPECTED_SCRIPT" ]] || die "not executable: ${EXPECTED_SCRIPT}"

LINK="$(readlink -f /usr/local/bin/ethpillar 2>/dev/null || true)"
[[ -n "$LINK" ]] || die "/usr/local/bin/ethpillar symlink missing"
[[ "$LINK" == "$EXPECTED_SCRIPT" ]] || die "symlink mismatch: got '${LINK}', want '${EXPECTED_SCRIPT}'"

command -v ethpillar >/dev/null || die "'ethpillar' not on PATH"
[[ "$(command -v ethpillar)" == "/usr/local/bin/ethpillar" ]] || die "unexpected ethpillar on PATH: $(command -v ethpillar)"

[[ -d "${EXPECTED_REPO}/.venv/bin" ]] || die "missing Python venv at ${EXPECTED_REPO}/.venv"
"${EXPECTED_REPO}/.venv/bin/python3" -c "import dotenv, requests, tqdm" \
  || die "Python runtime dependencies missing from venv"

BASE_DIR="$(
  cd "$EXPECTED_REPO"
  bash -c 'source ./functions.sh >/dev/null; printf "%s" "$BASE_DIR"'
)"
[[ "$BASE_DIR" == "$EXPECTED_REPO" ]] || die "BASE_DIR mismatch: got '${BASE_DIR}', want '${EXPECTED_REPO}'"

if [[ -n "$FORBID_REPO" && -f "${FORBID_REPO}/ethpillar.sh" ]]; then
  FORBID_SCRIPT="$(readlink -f "${FORBID_REPO}/ethpillar.sh")"
  [[ "$LINK" != "$FORBID_SCRIPT" ]] || die "symlink must not point at forbidden repo ${FORBID_REPO}"
fi

echo "OK: install verified for ${EXPECTED_REPO}"
