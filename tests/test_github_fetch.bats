#!/usr/bin/env bats
#
# The shell helper only execs `python -m deploy.http_retry`.
# Retry behavior is covered in tests/test_http_retry.py.
#

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  export PYTHON_LOG="$BATS_TEST_TMPDIR/python.log"
  : > "$PYTHON_LOG"
  unset ETHPILLAR_PYTHON
  unset PYTHONPATH
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../helpers/github_fetch.sh"
}

_write_python_stub() {
  local stub="$1"
  local code="${2:-0}"
  local body="${3:-}"
  cat > "$stub" <<EOF
#!/bin/bash
printf 'ARGS:%s\n' "\$*" >> "$PYTHON_LOG"
printf 'PYPATH:%s\n' "\$PYTHONPATH" >> "$PYTHON_LOG"
printf '%s' "$body"
exit $code
EOF
  chmod +x "$stub"
}

@test "github_curl execs python -m deploy.http_retry fetch" {
  local stub="$BATS_TEST_TMPDIR/python"
  _write_python_stub "$stub" 0 "hello-body"
  export ETHPILLAR_PYTHON="$stub"
  export BASE_DIR="/tmp/ethpillar-root"
  status=0
  github_curl "https://api.github.com/repos/flashbots/mev-boost/releases/latest" > "$BATS_TEST_TMPDIR/out" || status=$?
  [ "$status" -eq 0 ]
  [ "$(cat "$BATS_TEST_TMPDIR/out")" = "hello-body" ]
  grep -Fqx 'ARGS:-m deploy.http_retry fetch https://api.github.com/repos/flashbots/mev-boost/releases/latest' "$PYTHON_LOG"
  grep -Fqx 'PYPATH:/tmp/ethpillar-root' "$PYTHON_LOG"
}

@test "github_curl forwards extra headers and returns the Python status" {
  local stub="$BATS_TEST_TMPDIR/python"
  _write_python_stub "$stub" 1 ""
  export ETHPILLAR_PYTHON="$stub"
  export BASE_DIR="/tmp/ethpillar-root"
  status=0
  github_curl -H "Accept: application/vnd.github+json" "https://api.github.com/repos/example/repo/tags" > "$BATS_TEST_TMPDIR/out" 2> "$BATS_TEST_TMPDIR/err" || status=$?
  [ "$status" -eq 1 ]
  [ ! -s "$BATS_TEST_TMPDIR/out" ]
  grep -Fq -- '-H Accept: application/vnd.github+json' "$PYTHON_LOG"
  grep -Fq 'deploy.http_retry fetch' "$PYTHON_LOG"
}

@test "github_download execs python -m deploy.http_retry download" {
  local stub="$BATS_TEST_TMPDIR/python"
  local dest="$BATS_TEST_TMPDIR/mev-boost.tar.gz"
  _write_python_stub "$stub" 0 ""
  export ETHPILLAR_PYTHON="$stub"
  export BASE_DIR="$BATS_TEST_DIRNAME/.."
  status=0
  github_download "https://github.com/flashbots/mev-boost/releases/download/v1.12/mev-boost.tar.gz" "$dest" || status=$?
  [ "$status" -eq 0 ]
  grep -Fqx "ARGS:-m deploy.http_retry download https://github.com/flashbots/mev-boost/releases/download/v1.12/mev-boost.tar.gz $dest" "$PYTHON_LOG"
}

@test "github_download rejects a missing destination without calling Python" {
  local stub="$BATS_TEST_TMPDIR/python"
  _write_python_stub "$stub" 0 "should-not-run"
  export ETHPILLAR_PYTHON="$stub"
  status=0
  github_download "https://example.invalid/file" "" > "$BATS_TEST_TMPDIR/out" 2> "$BATS_TEST_TMPDIR/err" || status=$?
  [ "$status" -eq 1 ]
  [ ! -s "$PYTHON_LOG" ]
  grep -q 'requires a URL and a destination' "$BATS_TEST_TMPDIR/err"
}

@test "github_curl uses the repo venv when ETHPILLAR_PYTHON is unset" {
  local root="$BATS_TEST_TMPDIR/repo"
  local stub="$root/.venv/bin/python3"
  mkdir -p "$root/.venv/bin"
  _write_python_stub "$stub" 0 "from-venv"
  unset ETHPILLAR_PYTHON
  export BASE_DIR="$root"
  status=0
  github_curl "https://raw.githubusercontent.com/sivel/speedtest-cli/master/speedtest.py" > "$BATS_TEST_TMPDIR/out" || status=$?
  [ "$status" -eq 0 ]
  [ "$(cat "$BATS_TEST_TMPDIR/out")" = "from-venv" ]
  grep -Fq 'deploy.http_retry fetch' "$PYTHON_LOG"
  grep -Fqx "PYPATH:$root" "$PYTHON_LOG"
}

@test "shell helper does not implement its own retry loop" {
  ! grep -Eq '(^|[[:space:]])curl([[:space:]]|$)' helpers/github_fetch.sh
  ! grep -q 'GITHUB_FETCH_BACKOFF' helpers/github_fetch.sh
  ! grep -q 'speed-time' helpers/github_fetch.sh
  grep -q 'deploy.http_retry' helpers/github_fetch.sh
}

@test "production scripts do not curl or wget GitHub hosts directly" {
  local hits
  hits=$(grep -RInE '(curl|wget).*(github\.com|githubusercontent\.com)|(github\.com|githubusercontent\.com).*(curl|wget)' \
    --include='*.sh' \
    --exclude='github_fetch.sh' \
    . \
    | grep -v '/tests/' \
    | grep -v 'github_curl' \
    | grep -v 'github_download' || true)
  [ -z "$hits" ]
}
