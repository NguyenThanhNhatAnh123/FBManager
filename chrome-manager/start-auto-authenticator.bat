@echo off
setlocal
cd /d "%~dp0"
echo This starts only Manager profiles with private Chromium and automatic Authenticator.
echo Close the current Manager server with Ctrl+C before continuing.
set "CM_PREPARE_AUTHENTICATOR=1"
call "%~dp0start.bat"
endlocal
