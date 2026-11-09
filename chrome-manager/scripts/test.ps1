param([switch]$Browser)
$ErrorActionPreference='Stop'
$projectRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$taskTemp=Join-Path $projectRoot '.runtime\tmp'
New-Item -ItemType Directory -Force -Path $taskTemp,(Join-Path $projectRoot 'logs') | Out-Null
$env:TEMP=$taskTemp
$env:TMP=$taskTemp
$env:TMPDIR=$taskTemp
& .\.venv\Scripts\python.exe (Join-Path $PSScriptRoot 'ensure_sqlalchemy.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$pytestArguments=@('-m','pytest','-q','--junitxml', (Join-Path $projectRoot 'logs\pytest-results.xml'))
if ($Browser) { $pytestArguments+='--run-browser' }
& .\.venv\Scripts\python.exe @pytestArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Push-Location frontend
try { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
finally { Pop-Location }
