@echo off
setlocal
"%~dp0..\chrome-manager\.venv\Scripts\python.exe" -I -B "%~dp0manage_users.py" bootstrap
exit /b %errorlevel%
