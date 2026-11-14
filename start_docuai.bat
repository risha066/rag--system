@echo off
title DocuAI - Persistent Server Watchdog
cd /d "%~dp0"

set PYTHON_EXE=C:\Users\risha\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

echo ===================================================
echo   DocuAI Persistent Server - Starting Watchdog
echo ===================================================
echo Backend will be available at: http://localhost:8000/
echo.

:loop
echo [%date% %time%] Checking port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    echo Terminating stale process on port 8000 (PID: %%a)...
    taskkill /f /pid %%a >nul 2>&1
)

echo [%date% %time%] Launching DocuAI Server...
"%PYTHON_EXE%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

echo.
echo [%date% %time%] Server stopped or crashed. Restarting in 3 seconds...
ping -n 4 127.0.0.1 >nul
goto loop
