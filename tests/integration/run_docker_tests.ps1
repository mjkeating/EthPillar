# Build the Docker image natively
Write-Host "Rebuilding Docker image..."
docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .

$scripts = @(
    "deploy-caplin-erigon.py",
    "deploy-lighthouse-reth.py",
    "deploy-lodestar-besu.py",
    "deploy-nimbus-nethermind.py",
    "deploy-teku-besu.py"
)

$jobs = @()

# Start background jobs for parallel execution
foreach ($script in $scripts) {
    Write-Host "Starting background test for $script..."
    $scriptBlock = {
        param($s, $p)
        docker run --rm -v "$p`:/ethpillar" ethpillar-rebuild python3 /ethpillar/tests/integration/run_inside_docker.py $s
    }
    $job = Start-Job -ScriptBlock $scriptBlock -ArgumentList $script, $pwd.Path
    $jobs += $job
}

Write-Host "Waiting for all parallel tests to complete..."
# Wait for completion and output the results as they come in
$jobs | Wait-Job | Receive-Job

# Check if any job failed
$failed = $false
foreach ($job in $jobs) {
    if ($job.State -ne 'Completed') {
        $failed = $true
    }
}

Write-Host "----------------------------------------"

if ($failed) {
    Write-Host "❌ One or more Docker integration tests failed." -ForegroundColor Red
    exit 1
} else {
    Write-Host "✅ All Docker integration tests passed in parallel execution!" -ForegroundColor Green
    exit 0
}
