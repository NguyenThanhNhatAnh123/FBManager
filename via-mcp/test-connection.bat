@echo off
"%~dp0..\chrome-manager\.venv\Scripts\python.exe" -I "%~dp0smoke.py" --live --check-codex-config
if errorlevel 3 (
  echo Chua xac minh cau hinh Codex. Xem checks.configuration trong .runtime\smoke-live.json.
) else if errorlevel 2 (
  echo MCP da nap 61 tools nhung backend khong doc duoc tu process nay. Kiem tra quyen loopback va server 8000.
) else if errorlevel 1 (
  echo Loi khoi dong hoac handshake MCP. Xem checks.transport; chua the ket luan backend da dung.
) else (
  echo Cau hinh CLI, transport 61 tools va doc backend da dat. Chat Desktop cu van can nap lai MCP.
)
echo Chi doc cache ung dung; bao cao chan doan duoc luu trong .runtime.
pause
