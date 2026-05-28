#!/bin/bash
# EthPillar Integration Test Orchestrator (Linux/WSL)
# ==================================================
#
# This script builds the test Docker image and runs the full matrix of client
# and network combinations. It generates an HTML report in the results directory.
#

set -e

# Parse arguments
MAX_CONCURRENT=1
while [[ $# -gt 0 ]]; do
    case $1 in
        --parallel)
            MAX_CONCURRENT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Create results directory
timestamp=$(date +"%Y-%m-%d_%H-%M-%S")
results_dir="$(pwd)/tests/integration/results/run_$timestamp"
mkdir -p "$results_dir"
echo "Results will be stored in: $results_dir"
echo "Max concurrent tests: $MAX_CONCURRENT"

# Clean up orphaned cache temp files
cache_dir="$(pwd)/tests/integration/cache"
if [ -d "$cache_dir" ]; then
    echo "Cleaning up orphaned cache temp files..."
    find "$cache_dir" -name "tmp*" -type f -delete 2>/dev/null || true
fi

# Build the image
echo "Rebuilding Docker image..."
docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .

# Docker flags required for systemd-in-Docker
DOCKER_SYSTEMD_FLAGS="--privileged --cgroupns=host --tmpfs /run --tmpfs /run/lock"

# Combos and variations
combos=(
    "Caplin-Erigon"
    "Lighthouse-Reth"
    "Lodestar-Besu"
    "Nimbus-Nethermind"
    "Teku-Besu"
)

# Reduced matrix: One full staking config, one minimal config. 
# VC-only and custom setups are handled in custom_tests.
# Upgrades are handled in upgrade_tests.
variations=(
    "--network HOLESKY --mev --config 'Solo Staking Node'"
    "--network SEPOLIA --config 'Full Node Only'"
)

# Custom setup tests
custom_tests=(
    "Geth-Lighthouse-Custom-Setup-SEPOLIA|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Geth --cc Lighthouse --vc Lighthouse --network SEPOLIA --mev --config 'Custom Setup'"
    "Nethermind-Grandine-Custom-Setup-SEPOLIA|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Nethermind --cc Grandine --vc Lighthouse --network SEPOLIA --mev --config 'Custom Setup'"
    "Prysm-Reth-Custom-Setup-SEPOLIA|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Reth --cc Prysm --vc Prysm --network SEPOLIA --mev --config 'Custom Setup'"
    "Teku-Besu-VC-Only-HOLESKY|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --combo Teku-Besu --network HOLESKY --config 'Validator Client Only' --vc_only_bn_address http://192.168.1.123:5052"
)

# Dedicated upgrade tests (run once per unique client pair)
upgrade_tests=(
    "Upgrade-Reth-Lighthouse|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Reth --cc Lighthouse --network SEPOLIA --config 'Full Node Only' --test-updates"
    "Upgrade-Besu-Teku|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Besu --cc Teku --network SEPOLIA --config 'Full Node Only' --test-updates"
    "Upgrade-Nethermind-Nimbus|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Nethermind --cc Nimbus --network EPHEMERY --config 'Full Node Only' --test-updates"
    "Upgrade-Erigon-Caplin|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Erigon --cc Caplin --network SEPOLIA --config 'Full Node Only' --test-updates"
    "Upgrade-Geth-Lodestar|python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Geth --cc Lodestar --network SEPOLIA --config 'Full Node Only' --test-updates"
)

# Use a temporary file to store results from parallel processes
results_db="$results_dir/results.tmp"
touch "$results_db"

pids=()

# Helper function to track concurrency
manage_concurrency() {
    if [ ${#pids[@]} -ge $MAX_CONCURRENT ]; then
        wait -n
        new_pids=()
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                new_pids+=("$pid")
            fi
        done
        pids=("${new_pids[@]}")
    fi
}

start_time=$(date +%s)

run_test() {
    local label="$1"
    local run_cmd="$2"
    local display_var="$3"
    
    local log_name="$(echo "$label" | sed 's/[^a-zA-Z0-9-]/_/g' | tr -s '_')"
    local log_file="$results_dir/${log_name}.log"
    local container_name="ep-test-$(echo "$log_name" | tr -dc '[:alnum:]-' | head -c 60)"

    echo "Starting background test for $label..."
    (
        set +e
        local test_start=$(date +%s)
        
        # Clean up any stale container with the same name before starting
        docker rm -f "$container_name" > /dev/null 2>&1
        
        # Start a persistent container with systemd as PID 1
        # shellcheck disable=SC2086
        docker run -d --name "$container_name" $DOCKER_SYSTEMD_FLAGS -v "$(pwd):/ethpillar" ethpillar-rebuild > /dev/null 2>&1
        local run_status=$?
        
        if [ $run_status -ne 0 ]; then
            local test_end=$(date +%s)
            local duration=$((test_end - test_start))
            echo "FAIL|$label|$display_var|${log_name}.log|${duration}s" >> "$results_db"
            echo "❌ Failed to start container for $label (Exit Code: $run_status)" | tee -a "$log_file"
            exit 0
        fi
        
        sleep 3  # wait for systemd to initialize
        
        # Run the test via exec
        eval "docker exec \"$container_name\" $run_cmd > \"$log_file\" 2>&1"
        local status=$?
        
        # Always clean up the container
        docker rm -f "$container_name" > /dev/null 2>&1
        
        local test_end=$(date +%s)
        local duration=$((test_end - test_start))
        
        if [ $status -eq 0 ]; then
            echo "PASS|$label|$display_var|${log_name}.log|${duration}s" >> "$results_db"
            echo "✅ Finished test $label in ${duration}s"
        else
            echo "FAIL|$label|$display_var|${log_name}.log|${duration}s" >> "$results_db"
            echo "❌ Failed test $label in ${duration}s"
        fi
        exit 0
    ) &
    pids+=($!)
    manage_concurrency
}

# 1. Run combos and variations matrix
for combo in "${combos[@]}"; do
    for var in "${variations[@]}"; do
        # Skip caplin for Holesky as it's not supported
        if [ "$combo" == "Caplin-Erigon" ] && [[ "$var" == *"HOLESKY"* ]]; then
            echo "Skipping Holesky test for Caplin ($combo) as it's unsupported."
            echo "SKIPPED|$combo|$var|-|0s" >> "$results_db"
            continue
        fi

        # Switch Nimbus/Nethermind to Ephemery
        actual_var="$var"
        if [ "$combo" == "Nimbus-Nethermind" ] && [[ "$var" == *"HOLESKY"* ]]; then
            actual_var="${var/HOLESKY/EPHEMERY}"
        fi
        
        run_test "$combo" "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --combo \"$combo\" $actual_var" "$actual_var"
    done
done

# 2. Run custom tests
for custom in "${custom_tests[@]}"; do
    label="${custom%%|*}"
    cmd="${custom#*|}"
    run_test "$label" "$cmd" "Custom"
done

# 3. Run upgrade tests
for custom in "${upgrade_tests[@]}"; do
    label="${custom%%|*}"
    cmd="${custom#*|}"
    run_test "$label" "$cmd" "Upgrade"
done

# Wait for any remaining parallel jobs to finish
wait "${pids[@]}"

end_time=$(date +%s)
total_duration=$((end_time - start_time))

echo "----------------------------------------"
echo "Generating HTML Report..."

# Generate HTML
total=$(cat "$results_db" | wc -l)
passed=$(grep "^PASS|" "$results_db" | wc -l)
failed_count=$(grep "^FAIL|" "$results_db" | wc -l)
skipped=$(grep "^SKIPPED|" "$results_db" | wc -l)

cat <<EOF > "$results_dir/index.html"
<!DOCTYPE html>
<html>
<head>
    <title>Integration Test Report - $timestamp</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; background: #f8f9fa; color: #333; }
        .container { max-width: 1200px; margin: auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h1 { color: #007bff; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        .summary { margin-bottom: 20px; font-weight: bold; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #dee2e6; padding: 12px; text-align: left; }
        th { background-color: #f1f3f5; color: #495057; }
        tr:hover { background-color: #f8f9fa; }
        .status-pass { color: #28a745; font-weight: bold; }
        .status-fail { color: #dc3545; font-weight: bold; }
        .status-skipped { color: #ffc107; font-weight: bold; }
        .status-skip { color: #ffc107; font-weight: bold; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .footer { margin-top: 30px; font-size: 0.9em; color: #6c757d; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Integration Test Report</h1>
        <div class="summary">
            Run Time: $timestamp <br>
            Total Duration: ${total_duration}s <br>
            Total Tests: $total <br>
            Passed: $passed <br>
            Failed: $failed_count <br>
            Skipped: $skipped
        </div>
        <table>
            <thead>
                <tr>
                    <th>Script</th>
                    <th>Variation</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Log</th>
                </tr>
            </thead>
            <tbody>
EOF

while IFS='|' read -r status script var log duration; do
    status_class="status-${status,,}"
    if [ "$status" == "SKIPPED" ]; then
        log_link="-"
    else
        log_link="<a href='$log' target='_blank'>View Log</a>"
    fi
    echo "<tr><td>$script</td><td>$var</td><td class='$status_class'>$status</td><td>$duration</td><td>$log_link</td></tr>" >> "$results_dir/index.html"
done < "$results_db"

cat <<EOF >> "$results_dir/index.html"
            </tbody>
        </table>
        <div class="footer">
            EthPillar Integration Suite
        </div>
    </div>
</body>
</html>
EOF

rm "$results_db"

echo "✅ Report generated: $results_dir/index.html"
echo "⏱️ Total runtime: ${total_duration}s"
echo "----------------------------------------"

if [ $failed_count -gt 0 ]; then
    echo "❌ $failed_count tests failed."
    exit 1
else
    echo "✅ All integration tests passed!"
    exit 0
fi
