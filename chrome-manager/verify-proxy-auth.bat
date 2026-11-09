@echo off
setlocal
cd /d "%~dp0"
set CM_VERIFY_PROXY_AUTH=1
call "%~dp0start.bat"
endlocal
