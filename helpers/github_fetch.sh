#!/bin/bash
# Thin wrapper around `python -m deploy.http_retry`.
# Retry policy, backoff, and status handling live in deploy/http_retry.py.
# stdout is the response body (fetch) or empty (download). Errors go to stderr.

# Repo root: BASE_DIR when a caller already resolved it, otherwise this file's parent.
_github_fetch_root() {
  if [[ -n "${BASE_DIR:-}" ]]; then
    printf '%s\n' "$BASE_DIR"
    return 0
  fi
  local src dir
  src="${BASH_SOURCE[0]}"
  dir="$(dirname "$src")"
  (cd "$dir/.." && pwd)
}

# Same interpreter EthPillar uses for deploy Python:
#   1. ETHPILLAR_PYTHON when set (venv python exported by ensure_python_deps)
#   2. ${root}/.venv/bin/python3 when that venv exists
#   3. python3 on PATH
_github_fetch_python() {
  local root py
  root="$(_github_fetch_root)" || return 1
  if [[ -n "${ETHPILLAR_PYTHON:-}" ]]; then
    py="$ETHPILLAR_PYTHON"
  elif [[ -x "${root}/.venv/bin/python3" ]]; then
    py="${root}/.venv/bin/python3"
  else
    py="python3"
  fi
  PYTHONPATH="${root}${PYTHONPATH:+:$PYTHONPATH}" "$py" -m deploy.http_retry "$@"
}

# github_curl [http_retry fetch args...] URL
# Example: github_curl "$URL"
#          github_curl -H "Accept: application/vnd.github+json" "$URL"
# Prints the body. Non-zero on failure. GITHUB_TOKEN is applied to api.github.com.
github_curl() {
  _github_fetch_python fetch "$@"
}

# github_download URL DEST [http_retry download args...]
# Atomically place the body at DEST via the Python CLI.
github_download() {
  local url="${1:-}"
  local dest="${2:-}"
  if [[ -z "$url" || -z "$dest" ]]; then
    echo "ERROR: github_download requires a URL and a destination path" >&2
    return 1
  fi
  shift 2
  _github_fetch_python download "$url" "$dest" "$@"
}
