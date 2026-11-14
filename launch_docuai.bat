@echo off
cd /d "%~dp0"

:: Check if server is already running on port 8000, start if needed
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (!(Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue)) { & '%~dp0start_detached.ps1' }"

:: Open DocuAI web dashboard in default browser
start http://localhost:8000/
exit
