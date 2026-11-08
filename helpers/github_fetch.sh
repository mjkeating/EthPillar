#!/bin/bash
# Bounded retries for GitHub HTTP (release assets, API lookups, raw content).
# Keep the numbers in sync with deploy/http_retry.py.
#
# Policy:
#   4 attempts
#   10s connect timeout
#   60s read/overall timeout for small API and raw responses (--max-time)
#   60s stall timeout for release archives (--speed-time, below 1 byte/s)
#   3600s overall cap per archive attempt
#   backoff 1s, 2s, 4s
#   retry curl transport failures (timeouts, connection errors), HTTP 429, and 5xx
#   fail immediately on HTTP 404 and other 4xx
#
# Messages go to stderr so $(github_curl ...) stays a clean body.
# A failed download never leaves DEST or DEST.partial behind from this attempt.
# An existing DEST is replaced only after a complete 2xx body.

GITHUB_FETCH_ATTEMPTS=4
GITHUB_FETCH_CONNECT_TIMEOUT=10
GITHUB_FETCH_READ_TIMEOUT=60
GITHUB_FETCH_API_MAX_TIME=60
GITHUB_FETCH_DOWNLOAD_MAX_TIME=3600
GITHUB_FETCH_BACKOFF="1 2 4"

_github_fetch_backoff() {
  local -a waits=()
  local index
  # shellcheck disable=SC2206
  read -r -a waits <<< "$GITHUB_FETCH_BACKOFF"
  index=$(( $1 - 1 ))
  if [[ "$index" -lt 0 ]]; then
    index=0
  fi
  if [[ "$index" -ge ${#waits[@]} ]]; then
    index=$(( ${#waits[@]} - 1 ))
  fi
  printf '%s\n' "${waits[$index]}"
}

# Retryable curl exits: resolve, connect, partial file, timeout (including
# --speed-time), TLS connect, empty reply, recv failure, HTTP/2 stream.
_github_fetch_curl_retryable() {
  case "$1" in
    6|7|18|28|35|52|56|92) return 0 ;;
    *) return 1 ;;
  esac
}

_github_fetch_http_retryable() {
  case "$1" in
    429|5[0-9][0-9]) return 0 ;;
    *) return 1 ;;
  esac
}

# _github_fetch_to DEST MAX_TIME [curl args...]
# On success DEST is the complete body. On failure DEST is unchanged and
# DEST.partial is removed.
_github_fetch_to() {
  local dest="$1"
  local max_time="$2"
  shift 2
  local partial="${dest}.partial"
  local attempt=1
  local curl_status http_code delay dir retry
  dir=$(dirname -- "$dest")
  if [[ ! -d "$dir" ]]; then
    mkdir -p -- "$dir" || return 1
  fi
  rm -f -- "$partial"
  while [[ "$attempt" -le "$GITHUB_FETCH_ATTEMPTS" ]]; do
    rm -f -- "$partial"
    curl_status=0
    http_code=$(
      curl -q -sS -L \
        --connect-timeout "$GITHUB_FETCH_CONNECT_TIMEOUT" \
        --max-time "$max_time" \
        --speed-time "$GITHUB_FETCH_READ_TIMEOUT" \
        --speed-limit 1 \
        --user-agent ethpillar \
        -w '%{http_code}' \
        -o "$partial" \
        "$@"
    ) || curl_status=$?
    http_code="${http_code//[!0-9]/}"
    if [[ -z "$http_code" ]]; then
      http_code=000
    fi
    if [[ "$curl_status" -eq 0 && "$http_code" =~ ^2[0-9][0-9]$ ]]; then
      if ! mv -f -- "$partial" "$dest"; then
        rm -f -- "$partial"
        echo "ERROR: could not move GitHub download into place: ${dest}" >&2
        return 1
      fi
      return 0
    fi
    rm -f -- "$partial"
    retry=0
    if [[ "$curl_status" -ne 0 ]]; then
      if _github_fetch_curl_retryable "$curl_status"; then
        retry=1
      fi
    else
      if [[ "$http_code" == "000" ]] || _github_fetch_http_retryable "$http_code"; then
        retry=1
      fi
    fi
    if [[ "$retry" -eq 0 || "$attempt" -eq "$GITHUB_FETCH_ATTEMPTS" ]]; then
      echo "ERROR: GitHub request failed after ${attempt} attempt(s) (curl exit ${curl_status}, HTTP ${http_code}): $*" >&2
      rm -f -- "$partial"
      return 1
    fi
    delay=$(_github_fetch_backoff "$attempt")
    echo "GitHub request failed (curl exit ${curl_status}, HTTP ${http_code}); retrying in ${delay}s (attempt ${attempt}/${GITHUB_FETCH_ATTEMPTS})..." >&2
    sleep "$delay"
    attempt=$(( attempt + 1 ))
  done
  rm -f -- "$partial"
  return 1
}

# github_curl [curl args...] URL
# Print the response body. Non-zero on failure, with the reason on stderr.
github_curl() {
  local tmp
  tmp=$(mktemp) || return 1
  if ! _github_fetch_to "$tmp" "$GITHUB_FETCH_API_MAX_TIME" "$@"; then
    rm -f -- "$tmp" "${tmp}.partial"
    return 1
  fi
  cat -- "$tmp"
  rm -f -- "$tmp"
}

# github_download URL DEST
# Atomically place a release asset or raw file at DEST.
github_download() {
  local url="$1"
  local dest="$2"
  if [[ -z "${url}" || -z "${dest}" ]]; then
    echo "ERROR: github_download requires a URL and a destination path" >&2
    return 1
  fi
  _github_fetch_to "$dest" "$GITHUB_FETCH_DOWNLOAD_MAX_TIME" "$url"
}
