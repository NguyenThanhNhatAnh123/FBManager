param([switch]$NoBrowser, [switch]$AutoAuthenticator)
$ErrorActionPreference = 'Stop'
$env:CM_STARTUP_ATTEMPT_ID = [guid]::NewGuid().ToString('D')
if ($env:CM_PREPARE_AUTHENTICATOR -eq '1') { $AutoAuthenticator = $true }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        $commandExitCode = $LASTEXITCODE
        $stepName = if ($Arguments[0] -eq '-m') { $Arguments[1] } else { [IO.Path]::GetFileName($Arguments[0]) }
        if ($Arguments -contains '--enable') { $stepName += ' --enable' }
        $diagnosticsDirectory = Join-Path $projectRoot 'logs'
        New-Item -ItemType Directory -Force -Path $diagnosticsDirectory | Out-Null
        $errorPath = Join-Path $diagnosticsDirectory 'startup-error.json'
        $preserveDiagnostics = $false
        if ($stepName -eq 'run.py' -and (Test-Path -LiteralPath $errorPath)) {
            try {
                $existingDiagnostics = Get-Content -LiteralPath $errorPath -Raw | ConvertFrom-Json
                $preserveDiagnostics = $existingDiagnostics.attempt_id -eq $env:CM_STARTUP_ATTEMPT_ID
            } catch { }
        }
        if (!$preserveDiagnostics) {
            @{ step=$stepName; exit_code=$commandExitCode; program=[IO.Path]::GetFileName($Program);
                attempt_id=$env:CM_STARTUP_ATTEMPT_ID; timestamp=[DateTimeOffset]::UtcNow.ToString('o') } |
                ConvertTo-Json | Set-Content -LiteralPath $errorPath -Encoding UTF8
        }
        throw "Step failed: $stepName (exit $commandExitCode). The original error is above; diagnostics: logs\startup-error.json"
    }
}

try {
    Write-Host 'Chrome Multi-Profile Manager - Windows startup' -ForegroundColor Green
    if ($AutoAuthenticator) { Write-Host 'Mode: automatic Authenticator with private Chromium (Manager only).' -ForegroundColor Cyan }
    # Keep installer temporary files in this writable project directory.
    $taskTemp = Join-Path $projectRoot '.runtime\tmp'
    New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp
    $env:TMPDIR = $taskTemp
    $pythonCandidates = @((Join-Path $projectRoot '.venv\Scripts\python.exe'))
    foreach ($name in @('python.exe','python3.exe','py.exe')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) { $pythonCandidates += $found.Source }
    }
    $pythonCandidates += (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.12-64\python.exe')
    foreach ($base in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), 'C:\Python312', 'C:\Python313', 'C:\Python314')) {
        if (Test-Path -LiteralPath $base) {
            $pythonCandidates += Get-ChildItem -LiteralPath $base -Filter python.exe -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
        }
    }
    $pythonExe = $null
    foreach ($candidate in ($pythonCandidates | Select-Object -Unique)) {
        if (!(Test-Path -LiteralPath $candidate)) { continue }
        try {
            $detected = & $candidate (Join-Path $PSScriptRoot 'runtime_info.py') python 2>$null
            if ($LASTEXITCODE -eq 0 -and $detected) { $pythonExe = [string]($detected | Select-Object -Last 1); break }
        } catch { }
    }
    if (!$pythonExe) { throw 'Install Python 3.12+ from python.org and enable Add Python to PATH. See README.md.' }
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Creating Python virtual environment...'
        Invoke-Checked $pythonExe @('-m','venv',(Join-Path $projectRoot '.venv'))
    }
    & $venvPython (Join-Path $PSScriptRoot 'runtime_info.py') dependencies
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing Python dependencies (Internet required on first run)...'
        & $venvPython (Join-Path $PSScriptRoot 'runtime_info.py') pip
        if ($LASTEXITCODE -ne 0) { Invoke-Checked $venvPython @('-m','ensurepip','--upgrade') }
        Invoke-Checked $venvPython @('-m','pip','install','--disable-pip-version-check','--retries','1','--timeout','15','-r',(Join-Path $projectRoot 'backend\requirements.txt'))
    }
    $startupStateJson = & $venvPython (Join-Path $PSScriptRoot 'startup_state.py') '--record'
    if ($LASTEXITCODE -ne 0) { throw 'Could not safely inspect the existing Manager. No second instance was started.' }
    $startupState = $startupStateJson | ConvertFrom-Json
    if ($startupState.state -eq 'running') {
        $dashboardPath = if ($env:CM_OPEN_DASHBOARD_PATH -eq '/pages/') { '/pages/' } else { '/' }
        $dashboardUrl = "http://127.0.0.1:$($startupState.port)$dashboardPath"
        Write-Host "Manager is already running (PID $($startupState.pid)): $dashboardUrl" -ForegroundColor Green
        if (!$NoBrowser) { Start-Process $dashboardUrl | Out-Null }
        exit 0
    }
    if ($startupState.state -eq 'busy') {
        throw 'The Manager lock is held but the existing host is not ready or could not be verified. Wait briefly and try again; do not delete manager.lock.'
    }
    if ($startupState.state -ne 'stopped') {
        throw "The configured port or Manager identity could not be verified ($($startupState.reason)). No second instance was started."
    }
    Invoke-Checked $venvPython @((Join-Path $PSScriptRoot 'ensure_sqlalchemy.py'))
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (!$node -or !$npm) { throw 'Install Node.js 22.12+ (or 24 LTS), then reopen this program.' }
    Invoke-Checked $node.Source @((Join-Path $PSScriptRoot 'check_node.cjs'))
    if ($env:CM_VERIFY_PROXY_AUTH -eq '1') {
        Write-Host 'Checking saved proxy authentication with local fixture servers and real private Chromium...' -ForegroundColor Cyan
        Invoke-Checked $node.Source @((Join-Path $projectRoot 'tests\proxy_bridge_integration.cjs'),'--browser')
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'data') | Out-Null
    $dependencyHash = (Get-FileHash -LiteralPath (Join-Path $projectRoot 'frontend\package.json') -Algorithm SHA256).Hash
    $hashPath = Join-Path $projectRoot 'data\frontend-dependencies.sha256'
    $storedHash = if (Test-Path -LiteralPath $hashPath) { [IO.File]::ReadAllText($hashPath).Trim() } else { '' }
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        if (!(Test-Path -LiteralPath 'node_modules\vite') -or $storedHash -ne $dependencyHash) {
            Write-Host 'Installing frontend dependencies...'
            Invoke-Checked $npm.Source @('install','--no-audit','--no-fund')
            [IO.File]::WriteAllText($hashPath,$dependencyHash)
        }
        Invoke-Checked $npm.Source @('run','build')
    } finally { Pop-Location }

    if ($AutoAuthenticator) {
        Invoke-Checked $venvPython @((Join-Path $PSScriptRoot 'prepare_authenticator.py'))
        $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.browsers'
        Write-Host 'Installing/checking private Chromium from the official Playwright distribution...'
        Invoke-Checked $venvPython @('-m','playwright','install','chromium')
        Invoke-Checked $venvPython @((Join-Path $PSScriptRoot 'prepare_authenticator.py'),'--enable')
    }

    # Respect .env and require a real browser installation. No implicit channel change.
    $browserChannel = & $venvPython (Join-Path $PSScriptRoot 'runtime_info.py') channel
    if ($browserChannel -eq 'chromium') {
        if (Test-Path -LiteralPath (Join-Path $projectRoot 'data\authenticator-auto.json')) {
            $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.browsers'
        }
        Write-Host 'Checking/downloading Playwright Chromium...'
        Invoke-Checked $venvPython @('-m','playwright','install','chromium')
    } elseif ($browserChannel -eq 'chrome') {
        $chromePaths = @(
            (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
            (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
        )
        if (!($chromePaths | Where-Object { Test-Path -LiteralPath $_ })) {
            throw 'Google Chrome was not found. Install Chrome, or set CM_BROWSER_CHANNEL= in .env to use Chromium.'
        }
    } else { throw 'CM_BROWSER_CHANNEL must be chrome or empty (Chromium).' }

    $port = & $venvPython (Join-Path $PSScriptRoot 'runtime_info.py') port
    Write-Host "Dashboard: http://127.0.0.1:$port" -ForegroundColor Green
    Write-Host 'Press Ctrl+C once and wait for browsers to close. Browser data is retained.'
    if ($NoBrowser) { $env:CM_OPEN_DASHBOARD = 'false' } else { $env:CM_OPEN_DASHBOARD = 'true' }
    Invoke-Checked $venvPython @((Join-Path $projectRoot 'backend\run.py'))
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
