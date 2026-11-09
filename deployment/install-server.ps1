param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [string]$ExistingRoot,
    [string]$PythonPath,
    [string]$PublicOrigin,
    [switch]$CheckOnly
)
$ErrorActionPreference='Stop'
$sourceRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
# Keep the caller's components until Python validates Win32 aliases. Normalizing
# here could erase a trailing-dot/space component before the safety check.
$targetRoot=$InstallRoot
if(!$PythonPath){
    $candidate=Get-Command python.exe -ErrorAction SilentlyContinue
    if(!$candidate){throw 'Install Python 3.12+ and reopen this terminal, or pass -PythonPath.'}
    $PythonPath=$candidate.Source
}
if(!(Test-Path -LiteralPath $PythonPath -PathType Leaf)){throw 'Python executable was not found.'}
$probe=@(& $PythonPath -I -c 'import sys; print(int(sys.version_info >= (3,12))); print(sys.executable)')
if($LASTEXITCODE -ne 0){throw 'Python runtime check failed.'}
if($probe.Count -ne 2){throw 'Python runtime check returned an invalid response.'}
$pythonInfo=[pscustomobject]@{supported=($probe[0] -eq '1');executable=[string]$probe[1]}
if(!$pythonInfo.supported){throw 'Python 3.12 or later is required.'}
$node=Get-Command node.exe -ErrorAction SilentlyContinue
if(!$node){Write-Host 'Node.js 22.12+ is required for schedule times and managed proxy bridges; no global installation is performed.'}
$arguments=@('-I','-B',(Join-Path $PSScriptRoot 'install.py'),'--source-root',$sourceRoot,'--install-root',$targetRoot,'--python',$pythonInfo.executable)
if($ExistingRoot){$arguments+=@('--existing-root',$ExistingRoot)}
if($PublicOrigin){$arguments+=@('--public-origin',$PublicOrigin)}
if($CheckOnly){$arguments+='--check-only'}
& $pythonInfo.executable @arguments
exit $LASTEXITCODE
