$ErrorActionPreference = 'Stop'
$mcpRoot = $PSScriptRoot
$managerPython = Join-Path (Split-Path $mcpRoot -Parent) 'chrome-manager\.venv\Scripts\python.exe'
$mcpEntry = Join-Path $mcpRoot 'server.py'
$mcpMedia = Join-Path (Split-Path $mcpRoot -Parent) 'mcp-media'
if (!(Test-Path -LiteralPath $managerPython -PathType Leaf) -or !(Test-Path -LiteralPath $mcpEntry -PathType Leaf)) {
    throw 'Khong tim thay Python/ma MCP. Giu via-mcp canh chrome-manager.'
}
$codexCommand = Get-Command codex -ErrorAction Stop
# Inspect only our named entry, capture its JSON privately, and never replace
# an unrelated server configuration or change Codex approval/security policy.
$previous = $null
$found = $false
try {
    $previous = & $codexCommand.Source mcp get via_manager --json 2>$null
    $found = $LASTEXITCODE -eq 0
} catch {
    # Windows PowerShell may turn a missing-entry stderr line into an error.
    # The official add command still validates its own config before writing.
    $found = $false
}
if ($found) {
    $existing = ($previous -join "`n") | ConvertFrom-Json
    if ($existing.transport.command -ne $managerPython -or $existing.transport.args -notcontains $mcpEntry) {
        throw 'Ten via_manager da duoc dung boi cau hinh khac. Kiem tra trong Codex Settings; script khong ghi de.'
    }
    Write-Host 'via_manager da duoc dang ky dung; giu nguyen cau hinh.'
} else {
    & $codexCommand.Source mcp add via_manager -- $managerPython -I $mcpEntry --port 8000 --media-root $mcpMedia
    if ($LASTEXITCODE -ne 0) { throw 'Codex chua luu duoc MCP. Xem loi phia tren.' }
}
Write-Host 'Xong. Khoi dong lai ket noi MCP trong Codex Settings (hoac mo lai Codex) de nap cong cu.'
Write-Host 'Giu chrome-manager/start.bat chay. Media duoc chon: mcp-media.'
Write-Host 'Neu tai video lon, dat tool_timeout_sec = 900 cho via_manager; mau o codex-config.toml.'
