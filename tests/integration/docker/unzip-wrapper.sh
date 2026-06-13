#!/bin/bash
export PYTHONPATH="/ethpillar"
python3 /ethpillar/tests/integration/extract_cache.py unzip "$@"
