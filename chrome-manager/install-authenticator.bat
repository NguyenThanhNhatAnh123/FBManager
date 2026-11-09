@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once to create the application environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\install_authenticator.py" --open-dashboard
set "result=%errorlevel%"
echo.
pause
exit /b %result%
