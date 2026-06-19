# Run act workflow locally via act
#
# Prerequisites:
#   - Docker Desktop running
#   - act: winget install nektos.act
#   - Optional: $env:GITHUB_TOKEN for API rate limits during the test matrix
#
# cache-smoke runs Docker-in-Docker with the same bind mounts as integration tests.
# On Windows, act translates /mnt/c/... paths for Docker Desktop automatically.
#
# Usage (from repo root; on Windows use Bypass — same as run_docker_tests.ps1):
#   pwsh -ExecutionPolicy Bypass -File .\tests\integration\act-smoke\run-act-integration.ps1 -Run -Job cache-smoke
#   pwsh -ExecutionPolicy Bypass -File .\tests\integration\act-smoke\run-act-integration.ps1 -Run -Job integration

[CmdletBinding()]
param(
    [switch]$Run,
    [Alias("JobOnly")]
    [ValidateSet("integration", "cache-smoke")]
    [string]$Job = "integration",
    [string]$Workflow = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Set-Location $RepoRoot

$act = Get-Command act -ErrorAction SilentlyContinue
if (-not $act) {
    $wingetAct = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\nektos.act_Microsoft.Winget.Source_8wekyb3d8bbwe\act.exe"
    if (Test-Path $wingetAct) { $act = $wingetAct } else { throw "act not found. Install with: winget install nektos.act" }
} else {
    $act = $act.Source
}

if (-not $Workflow) {
    if ($Job -eq "cache-smoke") {
        $Workflow = ".github/workflows/act-integration-smoke.yml"
    } else {
        $Workflow = ".github/workflows/integration-test.yml"
    }
}

$event = if ($Job -eq "cache-smoke") { "workflow_dispatch" } else { "workflow_call" }

$args = @(
    "-P", "ubuntu-latest=catthehacker/ubuntu:act-latest",
    "--container-architecture", "linux/amd64",
    "--container-options", "--privileged",
    "-W", $Workflow,
    "-j", $Job
)

if ($Job -eq "integration") {
    $args += @("--input", "artifact_name_prefix=act-local")
}

if ($Run) {
    $args = @("-v") + $args
} else {
    $args = @("-n", "-v") + $args
}

if ($env:GITHUB_TOKEN) {
    $args += @("-s", "GITHUB_TOKEN=$($env:GITHUB_TOKEN)")
}

Write-Host "act $event $Workflow (job: $Job)" -ForegroundColor Cyan
if ($env:GITHUB_TOKEN) {
    Write-Host "Command: & '$act' $event ... -s GITHUB_TOKEN=***" -ForegroundColor DarkGray
} else {
    Write-Host "Command: & '$act' $event $($args -join ' ')" -ForegroundColor DarkGray
    Write-Host "WARNING: GITHUB_TOKEN not set; integration matrix may hit API rate limits." -ForegroundColor Yellow
}

& $act $event @args
exit $LASTEXITCODE
