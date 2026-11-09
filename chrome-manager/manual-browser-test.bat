@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run start.bat once to install this application first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\manual_browser_diagnostic.py" --profile-id 1
if errorlevel 1 pause
endlocal
