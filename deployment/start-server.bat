@echo off
setlocal
powershell.exe -NoLogo -NoProfile -File "%~dp0start-server.ps1" %*
exit /b %errorlevel%
