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

$variations = @(
    "--network HOLESKY --mev --config `"Solo Staking Node`"",
    "--network SEPOLIA --config `"Full Node Only`"",
    "--network HOLESKY --mev --config `"Lido CSM Staking Node`"",
    "--network HOLESKY --mev --config `"Lido CSM Validator Client Only`" --vc_only_bn_address http://192.168.1.123:5052",
    "--network HOLESKY --mev --config `"Validator Client Only`" --vc_only_bn_address http://192.168.1.123:5052",
    "--network HOLESKY --mev --config `"Failover Staking Node`""
)

$jobs = @()
$maxConcurrent = 5

foreach ($script in $scripts) {
    foreach ($var in $variations) {
        
        # Skip caplin for Holesky as it's not supported
        if ($script -eq "deploy-caplin-erigon.py" -and $var -match "holesky") {
            Write-Host "Skipping Holesky test for Caplin ($script) as it's unsupported." -ForegroundColor Yellow
            continue
        }

        Write-Host "Starting background test for $script [ $var ]..." -ForegroundColor Cyan
        
        $job = Start-Job -ScriptBlock {
            param($ScriptToRun, $VariationArgs, $WorkingDir)
            Set-Location -Path $WorkingDir
            
            # Using Invoke-Expression to correctly handle the quoted arguments inside variation
            Invoke-Expression "docker run --rm -v `"$WorkingDir`:/ethpillar`" ethpillar-rebuild python3 /ethpillar/tests/integration/run_inside_docker.py $ScriptToRun $VariationArgs"
            if ($LASTEXITCODE -ne 0) { throw "Integration test failed for $ScriptToRun $VariationArgs" }
        } -ArgumentList $script, $var, $pwd.Path
        
        $jobs += $job
        
        $runningJobs = $jobs | Where-Object { $_.State -eq 'Running' }
        while ($runningJobs.Count -ge $maxConcurrent) {
            Start-Sleep -Seconds 2
            $runningJobs = $jobs | Where-Object { $_.State -eq 'Running' }
        }
    }
}

Write-Host "Waiting for all parallel tests to complete..."
# Wait for completion and output the results as they come in
$jobs | Wait-Job | Receive-Job

# Check if any job failed
$failed = $false
foreach ($job in $jobs) {
    if ($job.State -ne 'Completed') {
        $scriptName = "Unknown"
        if ($job.ChildJobs.Count -gt 0 -and $job.ChildJobs[0].JobParameters.ArgumentList.Count -gt 0) {
            $scriptName = $job.ChildJobs[0].JobParameters.ArgumentList[0]
        }
        Write-Host "❌ Job for $scriptName failed with state: $($job.State)" -ForegroundColor Red
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
