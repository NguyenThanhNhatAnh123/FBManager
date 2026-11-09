@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once to prepare Python dependencies.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\check_outlook.py --deep
echo.
echo Diagnostics finished. Keep the Manager server running.
pause
endlocal
