param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$managerRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $managerRoot
$runtimeDirectory = Join-Path $managerRoot '.runtime'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$managerTemp = Join-Path $runtimeDirectory 'tmp'
New-Item -ItemType Directory -Path $managerTemp -Force | Out-Null
$env:TEMP = $managerTemp
$env:TMP = $managerTemp
$env:TMPDIR = $managerTemp
$managerPython = Join-Path $managerRoot '.venv\Scripts\python.exe'

function Invoke-ManagerChecked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Page Manager startup step failed (exit $LASTEXITCODE)." }
}

try {
    Write-Host 'Page Manager · Independent Meta workspace' -ForegroundColor Cyan
    if (!(Test-Path -LiteralPath $managerPython)) {
        $basePython = Get-Command python.exe -ErrorAction SilentlyContinue
        if (!$basePython) { $basePython = Get-Command py.exe -ErrorAction SilentlyContinue }
        if (!$basePython) { throw 'Install Python 3.12+ from python.org, then open start.bat again.' }
        Invoke-ManagerChecked $basePython.Source @('-m','venv',(Join-Path $managerRoot '.venv'))
    }
    & $managerPython -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("pip") else 1)'
    if ($LASTEXITCODE -ne 0) { Invoke-ManagerChecked $managerPython @('-m','ensurepip','--upgrade') }
    Invoke-ManagerChecked $managerPython @('-c','import sys; assert sys.version_info >= (3,12), "Python 3.12+ is required."')
    $requirementsPath = Join-Path $managerRoot 'requirements.txt'
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
    $stampPath = Join-Path $runtimeDirectory 'requirements.sha256'
    $savedHash = if (Test-Path -LiteralPath $stampPath) { [IO.File]::ReadAllText($stampPath).Trim() } else { '' }
    & $managerPython -c 'import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ("fastapi","uvicorn","httpx","filelock","dotenv","tzdata","PIL")) else 1)'
    $dependenciesReady = $LASTEXITCODE -eq 0
    if (!$dependenciesReady -or $savedHash -ne $requirementsHash) {
        Write-Host 'Preparing the independent Python environment (Internet required on first run)...'
        Invoke-ManagerChecked $managerPython @('-m','pip','install','--disable-pip-version-check','--retries','1','--timeout','20','-r',$requirementsPath)
        [IO.File]::WriteAllText($stampPath,$requirementsHash)
    }
    if ($NoBrowser) { $env:MPM_OPEN_BROWSER = 'false' }
    Write-Host 'Default dashboard: http://127.0.0.1:8010/' -ForegroundColor Green
    Write-Host 'Chrome Manager data is not read or migrated. Press Ctrl+C to stop Page Manager.'
    Invoke-ManagerChecked $managerPython @((Join-Path $managerRoot 'backend\run.py'))
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
