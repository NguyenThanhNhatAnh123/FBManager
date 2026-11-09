@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once to prepare Python dependencies.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\use_standard_chrome.py
if errorlevel 1 (
  echo Browser migration stopped. Existing profile data was not reset.
  pause
  exit /b 1
)
set CM_BROWSER_CHANNEL=chrome
echo Checking public Outlook endpoints through the saved proxies. No account is used.
".venv\Scripts\python.exe" -X utf8 scripts\check_outlook.py
if errorlevel 1 echo Outlook connection diagnostics could not complete. See logs\outlook-connectivity-validation.json.
call start.bat
endlocal
