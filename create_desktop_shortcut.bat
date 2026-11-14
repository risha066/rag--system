@echo off
set SCRIPT_DIR=%~dp0
set VBS_TARGET=%SCRIPT_DIR%run_background.vbs
set DESKTOP_DIR=%USERPROFILE%\Desktop
set SHORTCUT_PATH=%DESKTOP_DIR%\DocuAI.lnk

echo Set ws = CreateObject("WScript.Shell") > "%TEMP%\mkslink_desktop.vbs"
echo Set s = ws.CreateShortcut("%SHORTCUT_PATH%") >> "%TEMP%\mkslink_desktop.vbs"
echo s.TargetPath = "wscript.exe" >> "%TEMP%\mkslink_desktop.vbs"
echo s.Arguments = """%VBS_TARGET%""" >> "%TEMP%\mkslink_desktop.vbs"
echo s.WorkingDirectory = "%SCRIPT_DIR%" >> "%TEMP%\mkslink_desktop.vbs"
echo s.Description = "Launch DocuAI Server (Persistent Background Runner)" >> "%TEMP%\mkslink_desktop.vbs"
echo s.Save >> "%TEMP%\mkslink_desktop.vbs"

cscript //nologo "%TEMP%\mkslink_desktop.vbs"
del "%TEMP%\mkslink_desktop.vbs" >nul 2>&1

if exist "%SHORTCUT_PATH%" (
    echo.
    echo ========================================================
    echo  [SUCCESS] DocuAI Desktop Shortcut Created!
    echo  Location: %SHORTCUT_PATH%
    echo ========================================================
    echo Double-click "DocuAI" on your desktop to launch anytime.
) else (
    echo [INFO] You can double-click "run_background.vbs" in this folder to launch DocuAI.
)
pause
