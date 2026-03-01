# 🐳 EthPillar Docker Test Harness
# This script builds and runs the integration test environment.

$ErrorActionPreference = "Stop"

# 🛠️ 1. Build the Docker Image
Write-Host "`n🔨 Building Test Environment (Ubuntu)..." -ForegroundColor Cyan
docker build -t ethpillar-test -f .\tests\integration\Dockerfile.test .

# 🚀 2. Run the Container and Execute the Integration Tests
Write-Host "`n🚀 Running Integration Tests in Container..." -ForegroundColor Green
Write-Host "This will run all 5 deploy scripts in a sandbox. State is destroyed when done.`n" -ForegroundColor Yellow

# Use -v to mount the current directory into the container
# Use --rm to automatically remove the container when finished
docker run --rm -v "${PWD}:/ethpillar" ethpillar-test python3 /ethpillar/tests/integration/run_inside_docker.py

Write-Host "`n✨ Integration test run finished. Container state was discarded." -ForegroundColor Green
