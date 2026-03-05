# Create results directory
timestamp=$(date +"%Y-%m-%d_%H-%M-%S")
results_dir="$(pwd)/tests/integration/results/run_$timestamp"
mkdir -p "$results_dir"
echo "Results will be stored in: $results_dir"

# Build the image
echo "Rebuilding Docker image..."
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

# Use a temporary file to store results from parallel processes
results_db="$results_dir/results.tmp"
touch "$results_db"

pids=()
MAX_CONCURRENT=5

for script in "${scripts[@]}"; do
    for var in "${variations[@]}"; do
        
        # Skip caplin for Holesky as it's not supported
        if [[ "$script" == "deploy-caplin-erigon.py" && "$var" == *"HOLESKY"* ]]; then
            echo "Skipping Holesky test for Caplin ($script) as it's unsupported."
            echo "SKIPPED|$script|$var|-" >> "$results_db"
            continue
        fi

        # Switch Nimbus/Nethermind to Ephemery for Holesky
        if [[ "$script" == "deploy-nimbus-nethermind.py" && "$var" == *"HOLESKY"* ]]; then
            echo "Switching Nimbus/Nethermind from HOLESKY to EPHEMERY for test: $script $var"
            var="${var//HOLESKY/EPHEMERY}"
        fi

        log_name="${script}_$(echo $var | tr -dc '[:alnum:]-')"
        log_file="$results_dir/${log_name}.log"

        echo "Starting background test for $script [ $var ]..."
        (
            eval "docker run --rm -v \"$(pwd):/ethpillar\" ethpillar-rebuild python3 /ethpillar/tests/integration/run_inside_docker.py $script $var" > "$log_file" 2>&1
            status=$?
            if [ $status -eq 0 ]; then
                echo "PASS|$script|$var|${log_name}.log" >> "$results_db"
            else
                echo "FAIL|$script|$var|${log_name}.log" >> "$results_db"
            fi
        ) &
        pids+=($!)
        
        if [ ${#pids[@]} -ge $MAX_CONCURRENT ]; then
            wait -n
            # Filter pids to those still running
            new_pids=()
            for pid in "${pids[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    new_pids+=("$pid")
                fi
            done
            pids=("${new_pids[@]}")
        fi
    done
done

# Wait for any remaining parallel jobs to finish
wait "${pids[@]}"

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
                    <th>Log</th>
                </tr>
            </thead>
            <tbody>
EOF

while IFS='|' read -r status script var log; do
    status_class="status-${status,,}"
    if [ "$status" == "SKIPPED" ]; then
        log_link="-"
    else
        log_link="<a href='$log' target='_blank'>View Log</a>"
    fi
    echo "<tr><td>$script</td><td>$var</td><td class='$status_class'>$status</td><td>$log_link</td></tr>" >> "$results_dir/index.html"
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
echo "----------------------------------------"

if [ $failed_count -gt 0 ]; then
    echo "❌ $failed_count tests failed."
    exit 1
else
    echo "✅ All integration tests passed!"
    exit 0
fi
