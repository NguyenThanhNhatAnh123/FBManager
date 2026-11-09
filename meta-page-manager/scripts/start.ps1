param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$managerRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$chromeRoot = Join-Path (Split-Path -Parent $managerRoot) 'chrome-manager'
$chromeStartup = Join-Path $chromeRoot 'scripts\start.ps1'
if (!(Test-Path -LiteralPath $chromeStartup)) { throw 'Chrome Manager host was not found. Use start-standalone.bat for a separate development server.' }
$port = 8000
$configFile = Join-Path $chromeRoot '.env'
if (Test-Path -LiteralPath $configFile) {
    foreach ($line in [IO.File]::ReadAllLines($configFile)) {
        if ($line -match '^\s*CM_PORT\s*=\s*([0-9]+)\s*$') { $port = [int]$Matches[1] }
    }
}
if ($env:CM_PORT -match '^[0-9]+$') { $port = [int]$env:CM_PORT }
if ($port -lt 1024 -or $port -gt 65535) { throw 'Chrome Manager port is invalid.' }
$workspaceUrl = "http://127.0.0.1:$port/pages/"
$hostReady = $false
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
        if ($response.Content -notmatch 'Chrome Multi-Profile Manager') { throw 'The configured port belongs to another application.' }
        $hostReady = $true
    }
} catch {
    if ($_.Exception.Message -eq 'The configured port belongs to another application.') { throw }
}
if ($hostReady) {
    Write-Host "Page Manager: $workspaceUrl" -ForegroundColor Cyan
    if (!$NoBrowser) { Start-Process -FilePath $workspaceUrl }
    Write-Host 'If the Page workspace is unavailable, restart Chrome Manager once to load the new /pages route.'
    exit 0
}
$env:CM_OPEN_DASHBOARD_PATH='/pages/'
if ($NoBrowser) { & $chromeStartup -NoBrowser } else { & $chromeStartup }
exit $LASTEXITCODE
