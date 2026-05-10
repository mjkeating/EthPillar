# EthPillar Docker Test Harness
# This script builds and runs the integration test environment.

$ErrorActionPreference = "Stop"

# 1. Build the Docker Image
Write-Host "Building Test Environment..."
docker build -t ethpillar-test -f ./integration/Dockerfile.test ..

# 2. Run the Integration Tests
Write-Host "Running Integration Tests..."

$combinations = @("Caplin-Erigon", "Lighthouse-Reth", "Lodestar-Besu", "Nimbus-Nethermind", "Teku-Besu", "Grandine-Nethermind")
$networks = @("SEPOLIA", "HOLESKY")
$configs = @("Solo Staking Node", "Full Node Only")

$rootPath = (Get-Item $PWD).Parent.FullName
$containerName = "ep-test-harness"

foreach ($combo in $combinations) {
    foreach ($network in $networks) {
        foreach ($config in $configs) {
            if ($combo -eq "Caplin-Erigon" -and $network -eq "HOLESKY") { continue }
            if ($combo -eq "Nimbus-Nethermind" -and $network -eq "HOLESKY") { continue }

            Write-Host "Testing: $combo | $network | $config"
            
            # Start persistent container with systemd
            $systemdFlags = @("--privileged", "--cgroupns=host", "--tmpfs", "/run", "--tmpfs", "/run/lock")
            docker run -d --name $containerName $systemdFlags -v "${rootPath}:/ethpillar" ethpillar-test

            # Execute test
            $mevFlag = ""
            if ($config -eq "Solo Staking Node") { $mevFlag = "--mev" }

            docker exec $containerName python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --combo $combo --config $config --network $network $mevFlag
            
            $exitCode = $LASTEXITCODE

            # Cleanup
            docker rm -f $containerName

            if ($exitCode -ne 0) {
                Write-Host "FAILED: $combo"
                exit 1
            }
        }
    }
}

Write-Host "All tests finished."
