@echo off
powershell.exe -NoProfile -File "%~dp0connect-codex.ps1"
if errorlevel 1 echo Khong hoan tat ket noi. Xem thong bao phia tren.
pause
