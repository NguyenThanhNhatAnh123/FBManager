param([Parameter(Mandatory=$true)][string]$Root,[int]$ExcludePid=0)
$ErrorActionPreference='Stop'
$resolvedRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$count=0
foreach($process in Get-CimInstance Win32_Process){
    if($process.ProcessId -eq $PID -or $process.ProcessId -eq $ExcludePid){continue}
    if($process.Name -notmatch '^(python(?:w|[0-9](?:\.[0-9]+)?)?|node|chrome|chromium|codex)\.exe$'){continue}
    if($process.CommandLine -and $process.CommandLine.IndexOf($resolvedRoot,[StringComparison]::OrdinalIgnoreCase) -ge 0){$count++}
}
@{count=$count} | ConvertTo-Json -Compress
