@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\.initialized" goto initialize
".venv\Scripts\python.exe" -I -c "import sys; assert sys.prefix != sys.base_prefix" >nul 2>&1
if errorlevel 1 goto initialize
goto run

:initialize
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Initialize-Venv.ps1"
if errorlevel 1 goto failed

:run
".venv\Scripts\python.exe" agent.py %*
set "result=%errorlevel%"
pause
exit /b %result%

:failed
echo Environment initialization failed. Agent was not started.
pause
exit /b 1
