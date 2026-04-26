# EthPillar Docker Test Harness
# This script builds and runs the integration test environment.

$ErrorActionPreference = "Stop"

# 1. Build the Docker Image
Write-Host "Building Test Environment..."
docker build -t ethpillar-test -f ./integration/Dockerfile.test ..

# 2. Run the Integration Tests
Write-Host "Running Integration Tests..."

$combinations = @("Caplin-Erigon", "Lighthouse-Reth", "Lodestar-Besu", "Nimbus-Nethermind", "Teku-Besu")
$networks = @("SEPOLIA", "HOLESKY")
$configs = @("Solo Staking Node", "Full Node Only")

$rootPath = (Get-Item $PWD).Parent.FullName

foreach ($combo in $combinations) {
    foreach ($network in $networks) {
        foreach ($config in $configs) {
            if ($combo -eq "Caplin-Erigon" -and $network -eq "HOLESKY") { continue }
            if ($combo -eq "Nimbus-Nethermind" -and $network -eq "HOLESKY") { continue }

            Write-Host "Testing: $combo | $network | $config"
            
            $mevFlag = $null
            if ($config -eq "Solo Staking Node") { $mevFlag = "--mev" }

            $cmdArgs = @("run", "--rm", "-v", "${rootPath}:/ethpillar", "ethpillar-test", "python3", "/ethpillar/tests/integration/run_inside_docker.py", "deploy/deploy-node.py", "--combo", $combo, "--config", $config, "--network", $network)
            if ($mevFlag) { $cmdArgs += $mevFlag }

            # Join args into a single string for Start-Process to handle spaces correctly
            $argString = ($cmdArgs | ForEach-Object { if ($_ -match ' ') { "`"$_`"" } else { $_ } }) -join ' '
            $process = Start-Process -FilePath "docker" -ArgumentList $argString -NoNewWindow -Wait -PassThru
            
            if ($process.ExitCode -ne 0) {
                Write-Host "FAILED: $combo"
                exit 1
            }
        }
    }
}

Write-Host "All tests finished."
