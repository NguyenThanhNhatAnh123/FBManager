param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python=Join-Path $root 'chrome-manager\.venv\Scripts\python.exe'
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw 'Run install-server.ps1 first.'}
$arguments=@('-I','-B',(Join-Path $PSScriptRoot 'start_server.py'),'--root',$root)
if($CheckOnly){$arguments+='--check-only'}
& $python @arguments
exit $LASTEXITCODE
