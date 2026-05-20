<#
EthPillar Integration Test Orchestrator (Windows)
================================================

This script builds the test Docker image and runs the full matrix of client 
and network combinations. It manages parallel execution and generates 
an HTML report in the results directory.
#>
# Create results directory
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$resultsDir = Join-Path $pwd.Path "tests/integration/results/run_$timestamp"
New-Item -ItemType Directory -Path $resultsDir -Force | Out-Null
Write-Host "Results will be stored in: $resultsDir" -ForegroundColor Gray

# Build the Docker image natively
Write-Host "Rebuilding Docker image..."
docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .

# Common docker flags required for systemd-in-Docker
# We use an array for proper argument passing in PowerShell
$DOCKER_SYSTEMD_FLAGS = @(
    "--privileged",
    "--cgroupns=host",
    "--tmpfs", "/run",
    "--tmpfs", "/run/lock"
)

$combos = @(
    "Caplin-Erigon",
    "Lighthouse-Reth",
    "Lodestar-Besu",
    "Nimbus-Nethermind",
    "Teku-Besu"
)

$variations = @(
    "--network HOLESKY --mev --config `"Solo Staking Node`" --test-updates",
    "--network SEPOLIA --config `"Full Node Only`" --test-updates",
    "--network HOLESKY --mev --config `"Lido CSM Staking Node`" --test-updates",
    "--network HOLESKY --mev --config `"Lido CSM Validator Client Only`" --vc_only_bn_address http://192.168.1.123:5052 --test-updates",
    "--network HOLESKY --mev --config `"Validator Client Only`" --vc_only_bn_address http://192.168.1.123:5052 --test-updates",
    "--network HOLESKY --mev --config `"Failover Staking Node`" --test-updates"
)

# Custom setup tests use run_inside_docker.py directly with --ec/--cc/--vc flags.
# These cover client combos without a dedicated deploy-*.py script (e.g. Geth Custom Setup).
$customTests = @(
    [PSCustomObject]@{
        Label     = "Geth-Lighthouse-Custom-Setup-SEPOLIA"
        DockerCmd = "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Geth --cc Lighthouse --vc Lighthouse --network SEPOLIA --mev --config `"Custom Setup`" --test-updates"
    },
    [PSCustomObject]@{
        Label     = "Geth-Teku-FullNodeOnly-SEPOLIA"
        DockerCmd = "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Geth --cc Teku --network SEPOLIA --config `"Full Node Only`" --test-updates"
    },
    [PSCustomObject]@{
        Label     = "Nethermind-Grandine-Custom-Setup-SEPOLIA"
        DockerCmd = "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Nethermind --cc Grandine --vc Lighthouse --network SEPOLIA --mev --config `"Custom Setup`" --test-updates"
    },
    [PSCustomObject]@{
        Label     = "Updates-Geth-Lodestar-SEPOLIA"
        DockerCmd = "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Geth --cc Lodestar --network SEPOLIA --config `"Full Node Only`" --test-updates"
    },
    [PSCustomObject]@{
        Label     = "Prysm-Reth-Custom-Setup-SEPOLIA"
        DockerCmd = "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Reth --cc Prysm --vc Prysm --network SEPOLIA --mev --config `"Custom Setup`" --test-updates"
    }
)

$testMetadata = @()
$jobs = @()
$maxConcurrent = 5

foreach ($combo in $combos) {
    foreach ($var in $variations) {
        $cleanVar = $var -replace '[^a-zA-Z0-9-]', '_'
        $logName = "$combo`_$cleanVar"
        
        $meta = [PSCustomObject]@{
            Script = $combo
            Variation = $var
            LogFile = "$logName.log"
            Status = "Pending"
            JobId = $null
        }

        # Skip caplin for Holesky as it's not supported
        if ($combo -eq "Caplin-Erigon" -and $var -match "HOLESKY") {
            Write-Host "Skipping Holesky test for Caplin ($combo) as it's unsupported." -ForegroundColor Yellow
            $meta.Status = "Skipped"
            $testMetadata += $meta
            continue
        }

        # Switch Nimbus/Nethermind to Ephemery, since they dropped Holesky support in 2025/2026
        if ($combo -eq "Nimbus-Nethermind" -and $var -match "HOLESKY") {
            $var = $var -replace "HOLESKY", "EPHEMERY"
            $meta.Variation = $var
        }

        Write-Host "Starting background test for $combo [ $var ]..." -ForegroundColor Cyan
        
        $job = Start-Job -ScriptBlock {
            param($ComboName, $VariationArgs, $WorkingDir, $ResultsPath, $LogName, $DockerFlags)
            Set-Location -Path $WorkingDir
            
            $logFile = Join-Path $ResultsPath "$LogName.log"
            $containerName = "ep-test-$LogName" -replace '[^a-zA-Z0-9-]', '-'
            
            try {
                # Start a persistent container with systemd as PID 1
                & docker run -d --name $containerName @DockerFlags -v "$($WorkingDir):/ethpillar" ethpillar-rebuild | Out-Null
                
                # Wait briefly for systemd to initialize
                Start-Sleep -Seconds 3
                
                # Run the test via exec using deploy/deploy-node.py
                $execCmd = "docker exec $containerName python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --combo $ComboName $VariationArgs"
                Invoke-Expression "$execCmd > `"$logFile`" 2>&1"
                $exitCode = $LASTEXITCODE
            } finally {
                & docker rm -f $containerName | Out-Null
            }
            
            if ($exitCode -ne 0) { throw "Integration test failed for $ComboName $VariationArgs" }
        } -ArgumentList $combo, $var, $pwd.Path, $resultsDir, $logName, $DOCKER_SYSTEMD_FLAGS
        
        $meta.JobId = $job.Id
        $testMetadata += $meta
        $jobs += $job
        
        $runningJobs = $jobs | Where-Object { $_.State -eq 'Running' }
        while ($runningJobs.Count -ge $maxConcurrent) {
            Start-Sleep -Seconds 2
            $runningJobs = $jobs | Where-Object { $_.State -eq 'Running' }
        }
    }
}

# Run custom tests (Geth and other combos without dedicated deploy-*.py scripts)
foreach ($customTest in $customTests) {
    $logName = $customTest.Label -replace '[^a-zA-Z0-9-]', '_'
    $dockerCmd = $customTest.DockerCmd

    $meta = [PSCustomObject]@{
        Script    = $customTest.Label
        Variation = "Custom"
        LogFile   = "$logName.log"
        Status    = "Pending"
        JobId     = $null
    }

    Write-Host "Starting background test for $($customTest.Label)..." -ForegroundColor Cyan

    $job = Start-Job -ScriptBlock {
        param($DockerCmd, $WorkingDir, $ResultsPath, $LogName, $DockerFlags)
        Set-Location -Path $WorkingDir
        $logFile = Join-Path $ResultsPath "$LogName.log"
        $containerName = "ep-test-$LogName" -replace '[^a-zA-Z0-9-]', '-'
        
        try {
            # Start a persistent container with systemd as PID 1
            & docker run -d --name $containerName @DockerFlags -v "$($WorkingDir):/ethpillar" ethpillar-rebuild | Out-Null
            Start-Sleep -Seconds 3
            
            # Run the test via exec (DockerCmd already includes 'python3 ... run_inside_docker.py ...')
            # Strip the 'docker run --rm ... ethpillar-rebuild' prefix and run just the python part
            $pythonCmd = $DockerCmd -replace '^.*ethpillar-rebuild\s+', ''
            $execCmd = "docker exec $containerName $pythonCmd"
            Invoke-Expression "$execCmd > `"$logFile`" 2>&1"
            $exitCode = $LASTEXITCODE
        } finally {
            & docker rm -f $containerName | Out-Null
        }
        
        if ($exitCode -ne 0) { throw "Custom integration test failed: $DockerCmd" }
    } -ArgumentList $dockerCmd, $pwd.Path, $resultsDir, $logName, $DOCKER_SYSTEMD_FLAGS

    $meta.JobId = $job.Id
    $testMetadata += $meta
    $jobs += $job

    $runningJobs = $jobs | Where-Object { $_.State -eq 'Running' }
    while ($runningJobs.Count -ge $maxConcurrent) {
        Start-Sleep -Seconds 2
        $runningJobs = $jobs | Where-Object { $_.State -eq 'Running' }
    }
}

Write-Host "Waiting for all parallel tests to complete..."
$jobs | Wait-Job | Out-Null

# Update statuses
foreach ($meta in $testMetadata) {
    if ($meta.Status -eq "Skipped") { continue }
    $job = $jobs | Where-Object { $_.Id -eq $meta.JobId }
    if ($job.State -eq "Completed") {
        $meta.Status = "PASS"
    } else {
        $meta.Status = "FAIL"
    }
}

# Generate HTML Report
$html = @"
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
            Total Tests: $($testMetadata.Count) <br>
            Passed: $(($testMetadata | Where-Object { $_.Status -eq "PASS" }).Count) <br>
            Failed: $(($testMetadata | Where-Object { $_.Status -eq "FAIL" }).Count) <br>
            Skipped: $(($testMetadata | Where-Object { $_.Status -eq "Skipped" }).Count)
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
"@

foreach ($meta in $testMetadata) {
    $statusClass = "status-$($meta.Status.ToLower())"
    $logLink = if ($meta.Status -ne "Skipped") { "<a href='$($meta.LogFile)' target='_blank'>View Log</a>" } else { "-" }
    $html += "<tr><td>$($meta.Script)</td><td>$($meta.Variation)</td><td class='$statusClass'>$($meta.Status)</td><td>$logLink</td></tr>`n"
}

$html += @"
            </tbody>
        </table>
        <div class="footer">
            EthPillar Integration Suite
        </div>
    </div>
</body>
</html>
"@

$htmlPath = Join-Path $resultsDir "index.html"
$html | Out-File $htmlPath
Write-Host "----------------------------------------"
Write-Host "✅ Report generated: $htmlPath" -ForegroundColor Green

$failedCount = ($testMetadata | Where-Object { $_.Status -eq "FAIL" }).Count
if ($failedCount -gt 0) {
    Write-Host "❌ $failedCount tests failed." -ForegroundColor Red
    exit 1
} else {
    Write-Host "✅ All integration tests passed!" -ForegroundColor Green
    exit 0
}
