#!/bin/bash
URL=""
OUT=""
for arg in "$@"; do
    if [[ "$arg" == http* ]]; then URL="$arg"; fi
    if [[ "$prev" == "-O" ]]; then OUT="$arg"; fi
    prev="$arg"
done
if [ -n "$URL" ] && [ -n "$OUT" ]; then
    export ENABLE_EP_CACHE=1
    export PYTHONPATH="/ethpillar/tests/integration:/ethpillar"
    python3 -c "
import sys
try:
    import requests
    r = requests.get(sys.argv[1], stream=True)
    r.raise_for_status()
    with open(sys.argv[2], 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
except Exception:
    sys.exit(1)
" "$URL" "$OUT"
    if [ $? -eq 0 ]; then exit 0; fi
fi
/usr/bin/wget "$@"
