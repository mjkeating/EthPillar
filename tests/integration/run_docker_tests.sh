#!/bin/bash

# Build the image
docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .

scripts=(
    "deploy-caplin-erigon.py"
    "deploy-lighthouse-reth.py"
    "deploy-lodestar-besu.py"
    "deploy-nimbus-nethermind.py"
    "deploy-teku-besu.py"
)

pids=()

for script in "${scripts[@]}"; do
    echo "Starting background test for $script..."
    docker run --rm -v "$(pwd):/ethpillar" ethpillar-rebuild python3 /ethpillar/tests/integration/run_inside_docker.py "$script" &
    pids+=($!)
done

failed=0

# Wait for all parallel jobs to finish
for pid in "${pids[@]}"; do
    wait $pid
    if [ $? -ne 0 ]; then
        failed=1
    fi
done

echo "----------------------------------------"

if [ $failed -eq 1 ]; then
    echo "❌ One or more Docker integration tests failed."
    exit 1
else
    echo "✅ All Docker integration tests passed in parallel execution!"
    exit 0
fi
