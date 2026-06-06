<#
EthPillar Integration Test Orchestrator (Windows Wrapper)
=========================================================

This script is a thin wrapper that invokes the Linux integration test script
via Windows Subsystem for Linux (WSL).

All integration test logic has been consolidated into:
tests/integration/run_docker_tests.py
#>

$ErrorActionPreference = "Stop"

# Check if WSL is available
try {
    wsl --status | Out-Null
} catch {
    Write-Host "❌ Error: Windows Subsystem for Linux (WSL) is required but not found." -ForegroundColor Red
    Write-Host "Please install WSL2 and configure Docker Desktop to use the WSL2 backend." -ForegroundColor Yellow
    exit 1
}

# Convert the current Windows path to a WSL path
# E.g. C:\Github\EthPillar -> /mnt/c/Github/EthPillar
$currentDir = $pwd.Path
# Use wslpath to get the exact mapping (handles different drives and setups)
$wslPath = wsl -e wslpath -u "$currentDir"
$wslPath = $wslPath.Trim()

Write-Host "🚀 Launching EthPillar Integration Tests via WSL..." -ForegroundColor Cyan
Write-Host "Working directory (WSL): $wslPath" -ForegroundColor Gray
Write-Host "----------------------------------------`n"

# Execute the bash script via WSL, passing along any arguments
# Use bash -c to ensure we start in the right directory and run the script
$argsStr = $args -join " "
wsl -e bash -c "cd '$wslPath' && bash tests/integration/run_docker_tests.sh $argsStr"

# Forward the exit code
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host "`n❌ Integration tests failed (Exit Code: $exitCode)." -ForegroundColor Red
    exit $exitCode
} else {
    Write-Host "`n✅ Integration tests completed successfully." -ForegroundColor Green
    exit 0
}
