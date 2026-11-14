@echo off
title Create DocuAI Desktop Shortcut
cd /d "%~dp0"

echo Creating DocuAI Desktop Shortcut...
powershell -ExecutionPolicy Bypass -File "%~dp0create_shortcuts.ps1"

echo.
echo ========================================================
echo  [SUCCESS] DocuAI Shortcut Created on your Desktop!
echo ========================================================
echo Look for "DocuAI" on your Windows desktop.
echo Double-clicking it will start DocuAI and open your browser!
echo.
pause
