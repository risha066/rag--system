@echo off
set SCRIPT_DIR=%~dp0
set VBS_TARGET=%SCRIPT_DIR%run_background.vbs
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_DIR%\DocuAI_Server.lnk

echo Set ws = CreateObject("WScript.Shell") > "%TEMP%\mkslink.vbs"
echo Set s = ws.CreateShortcut("%SHORTCUT_PATH%") >> "%TEMP%\mkslink.vbs"
echo s.TargetPath = "wscript.exe" >> "%TEMP%\mkslink.vbs"
echo s.Arguments = """%VBS_TARGET%""" >> "%TEMP%\mkslink.vbs"
echo s.WorkingDirectory = "%SCRIPT_DIR%" >> "%TEMP%\mkslink.vbs"
echo s.Save >> "%TEMP%\mkslink.vbs"

cscript //nologo "%TEMP%\mkslink.vbs"
del "%TEMP%\mkslink.vbs" >nul 2>&1

if exist "%SHORTCUT_PATH%" (
    echo [SUCCESS] DocuAI will now start automatically whenever Windows boots!
    echo Shortcut: %SHORTCUT_PATH%
) else (
    echo [ERROR] Failed to create shortcut.
)
