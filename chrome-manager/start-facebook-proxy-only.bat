@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once to prepare dependencies.
  pause
  exit /b 1
)
echo Checking Facebook-only routing with owned local fixture servers and installed Google Chrome...
node.exe tests\proxy_routing_unit.cjs
if errorlevel 1 goto failed
node.exe tests\facebook_routing_integration.cjs --browser
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 scripts\enable_facebook_routing.py
if errorlevel 1 goto failed
call start.bat
exit /b %errorlevel%
:failed
echo Routing setup did not complete. See the error above and logs\facebook-routing-validation.json.
pause
exit /b 1
