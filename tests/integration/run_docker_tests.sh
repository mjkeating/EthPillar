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

variations=(
    "--network HOLESKY --mev --config 'Solo Staking Node'"
    "--network SEPOLIA --config 'Full Node Only'"
    "--network HOLESKY --mev --config 'Lido CSM Staking Node'"
    "--network HOLESKY --mev --config 'Lido CSM Validator Client Only' --vc_only_bn_address http://192.168.1.123:5052"
    "--network HOLESKY --mev --config 'Validator Client Only' --vc_only_bn_address http://192.168.1.123:5052"
    "--network HOLESKY --mev --config 'Failover Staking Node'"
)

pids=()
MAX_CONCURRENT=5

failed=0

for script in "${scripts[@]}"; do
    for var in "${variations[@]}"; do
        
        # Skip caplin for Holesky as it's not supported
        if [[ "$script" == "deploy-caplin-erigon.py" && "$var" == *"holesky"* ]]; then
            echo "Skipping Holesky test for Caplin ($script) as it's unsupported."
            continue
        fi

        echo "Starting background test for $script [ $var ]..."
        # We need to evaluate the var so quotes are handled correctly
        eval "docker run --rm -v \"$(pwd):/ethpillar\" ethpillar-rebuild python3 /ethpillar/tests/integration/run_inside_docker.py $script $var &"
        pids+=($!)
        
        if [ ${#pids[@]} -ge $MAX_CONCURRENT ]; then
            wait -n ${pids[@]}
            # clear completed pids (simplified tracking)
            new_pids=()
            for pid in "${pids[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    new_pids+=("$pid")
                else
                    wait "$pid"
                    if [ $? -ne 0 ]; then failed=1; fi
                fi
            done
            pids=("${new_pids[@]}")
        fi
    done
done

# Wait for any remaining parallel jobs to finish
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
