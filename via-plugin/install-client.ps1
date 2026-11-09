#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [string]$MarketplaceRoot = [Environment]::GetFolderPath('UserProfile'),
    [string]$InstallRoot,
    [string]$PythonExe,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$')][string]$ProfileName = 'default',
    [Security.SecureString]$AccessToken,
    [switch]$AllowLoopbackHttp,
    [switch]$PortableLayout
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is required to store a private Windows credential.' }
$MarketplaceRoot = [IO.Path]::GetFullPath($MarketplaceRoot)
if (-not $InstallRoot) { $InstallRoot = Join-Path $MarketplaceRoot '.codex\plugins\via' }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$credentialFile = Join-Path $env:LOCALAPPDATA "ViaCodex\credentials\$ProfileName\token.bin"

$candidates = @()
if ($PythonExe) {
    $candidates += @{ Command = $PythonExe; Args = @() }
} else {
    foreach ($name in @('python.exe', 'python3.exe', 'py.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += @{ Command = $command.Source; Args = @() } }
    }
}
$resolvedPython = $null
foreach ($candidate in $candidates) {
    try {
        $probeArgs = @($candidate.Args) + @('-I', '-B', '-c', 'import sys; assert sys.version_info >= (3, 12); print(sys.executable)')
        $candidatePath = & $candidate.Command @probeArgs 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidatePath -and (Test-Path -LiteralPath ([string]$candidatePath) -PathType Leaf)) {
            $resolvedPython = ([string]$candidatePath).Trim()
            break
        }
    } catch { }
}
if (-not $resolvedPython) { throw 'Install Python 3.12 or newer, then rerun with -PythonExe pointing to python.exe.' }
if (-not $AccessToken) {
    Write-Host 'Log into your own Via account, change the initial password, then create a personal token at /auth/account.'
    $AccessToken = Read-Host 'Personal Via token (hidden)' -AsSecureString
}
$secretPointer = [IntPtr]::Zero
$plainToken = $null
$inputJson = $null
try {
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($AccessToken)
    $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    $spec = @{
        server_url = $ServerUrl
        marketplace_root = $MarketplaceRoot
        install_root = $InstallRoot
        credential_file = [IO.Path]::GetFullPath($credentialFile)
        python_exe = $resolvedPython
        token = $plainToken
        allow_loopback_http = [bool]$AllowLoopbackHttp
        portable_layout = [bool]$PortableLayout
    }
    $inputJson = $spec | ConvertTo-Json -Compress
    # The token travels over a private process pipe, never argv or an environment variable.
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $resolvedPython
    $installer = Join-Path $PSScriptRoot 'client\install.py'
    if ($installer.Contains('"')) { throw 'Invalid source path.' }
    $info.Arguments = '-I -B "' + $installer + '"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    $outTask = $process.StandardOutput.ReadToEndAsync()
    $errTask = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write($inputJson)
    $process.StandardInput.Close()
    $process.WaitForExit()
    $outputText = $outTask.GetAwaiter().GetResult()
    $errorText = $errTask.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) { throw 'Client installation failed. Verify the URL, token, permissions and destination; the old installation was preserved or rolled back.' }
    $result = $outputText | ConvertFrom-Json
    Write-Host ('Installed Via plugin: ' + $result.plugin_root)
    Write-Host ('Local marketplace: ' + $result.marketplace)
    Write-Host 'Restart Codex, open Plugins, and enable Via from the local marketplace.'
    Write-Host ('CLI: enable ' + $result.plugin_id + ' in your own Codex configuration if required.')
    if ($result.previous_plugin) { Write-Host ('Previous plugin retained: ' + $result.previous_plugin) }
} finally {
    if ($secretPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer) }
    $plainToken = $null
    $inputJson = $null
    if (Get-Variable spec -ErrorAction SilentlyContinue) { $spec.token = $null }
    if (Get-Variable process -ErrorAction SilentlyContinue) { $process.Dispose() }
}
