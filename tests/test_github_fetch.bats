#!/usr/bin/env bats
#
# Behavioral tests for helpers/github_fetch.sh. curl and sleep are stubbed.
#

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  export CURL_LOG="$BATS_TEST_TMPDIR/curl.log"
  export SLEEP_LOG="$BATS_TEST_TMPDIR/sleep.log"
  export CURL_PLAN="$BATS_TEST_TMPDIR/plan"
  : > "$CURL_LOG"
  : > "$SLEEP_LOG"
  : > "$CURL_PLAN"
  # shellcheck disable=SC1091
  source "$BATS_TEST_DIRNAME/../helpers/github_fetch.sh"

  curl() {
    printf '%s\n' "$*" >> "$CURL_LOG"
    local out="" prev="" arg
    for arg in "$@"; do
      if [[ "$prev" == "-o" ]]; then
        out="$arg"
      fi
      prev="$arg"
    done
    local line exit_code http_code body
    line=$(head -n 1 "$CURL_PLAN")
    tail -n +2 "$CURL_PLAN" > "$CURL_PLAN.next"
    mv "$CURL_PLAN.next" "$CURL_PLAN"
    # shellcheck disable=SC2086
    read -r exit_code http_code body <<< "$line"
    if [[ -n "$out" ]]; then
      printf '%s' "$body" > "$out"
    fi
    printf '%s' "$http_code"
    return "$exit_code"
  }

  sleep() {
    printf '%s\n' "$1" >> "$SLEEP_LOG"
  }
}

@test "github_curl returns a 2xx body on the first attempt" {
  printf '0 200 hello-body\n' > "$CURL_PLAN"
  status=0
  github_curl "https://api.github.com/repos/flashbots/mev-boost/releases/latest" > "$BATS_TEST_TMPDIR/out" || status=$?
  [ "$status" -eq 0 ]
  [ "$(cat "$BATS_TEST_TMPDIR/out")" = "hello-body" ]
  [ ! -s "$SLEEP_LOG" ]
  grep -q -- '--connect-timeout 10' "$CURL_LOG"
  grep -q -- '--max-time 60' "$CURL_LOG"
  grep -q -- '--user-agent ethpillar' "$CURL_LOG"
  [ "$(wc -l < "$CURL_LOG")" -eq 1 ]
}

@test "github_curl retries a timeout then returns only the successful body" {
  printf '28 000 partial\n0 200 ready\n' > "$CURL_PLAN"
  status=0
  github_curl "https://api.github.com/repos/example/repo/releases/latest" > "$BATS_TEST_TMPDIR/out" || status=$?
  [ "$status" -eq 0 ]
  [ "$(cat "$BATS_TEST_TMPDIR/out")" = "ready" ]
  [ "$(cat "$SLEEP_LOG")" = "1" ]
  [ "$(wc -l < "$CURL_LOG")" -eq 2 ]
}

@test "github_curl retries HTTP 503 and fails fast on HTTP 404" {
  printf '0 503 busy\n0 200 ok\n' > "$CURL_PLAN"
  status=0
  github_curl "https://api.github.com/repos/example/repo/tags" > "$BATS_TEST_TMPDIR/out" || status=$?
  [ "$status" -eq 0 ]
  [ "$(cat "$BATS_TEST_TMPDIR/out")" = "ok" ]
  [ "$(cat "$SLEEP_LOG")" = "1" ]

  : > "$SLEEP_LOG"
  : > "$CURL_LOG"
  printf '0 404 missing\n0 200 should-not-run\n' > "$CURL_PLAN"
  status=0
  github_curl "https://api.github.com/repos/example/repo/releases/tags/v9" > "$BATS_TEST_TMPDIR/out" 2> "$BATS_TEST_TMPDIR/err" || status=$?
  [ "$status" -eq 1 ]
  [ ! -s "$BATS_TEST_TMPDIR/out" ]
  [ ! -s "$SLEEP_LOG" ]
  grep -q 'HTTP 404' "$BATS_TEST_TMPDIR/err"
  [ "$(wc -l < "$CURL_LOG")" -eq 1 ]
}

@test "github_curl reports the final attempt after repeated timeouts" {
  printf '28 000\n28 000\n28 000\n28 000\n' > "$CURL_PLAN"
  status=0
  github_curl "https://api.github.com/repos/example/repo/releases/latest" > "$BATS_TEST_TMPDIR/out" 2> "$BATS_TEST_TMPDIR/err" || status=$?
  [ "$status" -eq 1 ]
  [ ! -s "$BATS_TEST_TMPDIR/out" ]
  [ "$(cat "$SLEEP_LOG")" = $'1\n2\n4' ]
  grep -q 'after 4 attempt' "$BATS_TEST_TMPDIR/err"
  [ "$(wc -l < "$CURL_LOG")" -eq 4 ]
}

@test "github_download replaces the destination only after a complete 2xx body" {
  local dest="$BATS_TEST_TMPDIR/mev-boost.tar.gz"
  printf 'old-good' > "$dest"
  printf '28 000 partial-bytes\n0 200 full-archive\n' > "$CURL_PLAN"
  status=0
  github_download "https://github.com/flashbots/mev-boost/releases/download/v1.12/mev-boost.tar.gz" "$dest" 2> "$BATS_TEST_TMPDIR/err" || status=$?
  [ "$status" -eq 0 ]
  [ "$(cat "$dest")" = "full-archive" ]
  [ ! -e "${dest}.partial" ]
  grep -q -- '--max-time 3600' "$CURL_LOG"
  grep -q -- '--speed-time 60' "$CURL_LOG"
  grep -q -- '--connect-timeout 10' "$CURL_LOG"
}

@test "github_download leaves an existing file in place on HTTP 404" {
  local dest="$BATS_TEST_TMPDIR/besu.tar.gz"
  printf 'previous-good' > "$dest"
  printf '0 404 missing\n' > "$CURL_PLAN"
  status=0
  github_download "https://github.com/besu-eth/besu/releases/download/26.10.2/besu.tar.gz" "$dest" > "$BATS_TEST_TMPDIR/out" 2> "$BATS_TEST_TMPDIR/err" || status=$?
  [ "$status" -eq 1 ]
  [ "$(cat "$dest")" = "previous-good" ]
  [ ! -e "${dest}.partial" ]
  [ ! -s "$SLEEP_LOG" ]
  grep -q 'HTTP 404' "$BATS_TEST_TMPDIR/err"
  grep -q 'after 1 attempt' "$BATS_TEST_TMPDIR/err"
}

@test "production scripts do not curl or wget GitHub hosts directly" {
  local hits
  # github_curl / github_download contain the letters "curl" but are the helper.
  hits=$(grep -RInE '(curl|wget).*(github\.com|githubusercontent\.com)|(github\.com|githubusercontent\.com).*(curl|wget)' \
    --include='*.sh' \
    --exclude='github_fetch.sh' \
    . \
    | grep -v '/tests/' \
    | grep -v 'github_curl' \
    | grep -v 'github_download' || true)
  [ -z "$hits" ]
}
