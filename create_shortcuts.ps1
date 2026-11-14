$wshell = New-Object -ComObject WScript.Shell
$vbsPath = "C:\Users\risha\.gemini\antigravity\scratch\rag--system\open_docuai.vbs"
$workDir = "C:\Users\risha\.gemini\antigravity\scratch\rag--system"

$desktopPaths = @(
    [Environment]::GetFolderPath("Desktop"),
    "C:\Users\risha\Desktop",
    "C:\Users\risha\OneDrive\Desktop"
) | Select-Object -Unique

foreach ($d in $desktopPaths) {
    if (Test-Path $d) {
        $lnkPath = Join-Path $d "DocuAI.lnk"
        $shortcut = $wshell.CreateShortcut($lnkPath)
        $shortcut.TargetPath = "wscript.exe"
        $shortcut.Arguments = "`"$vbsPath`""
        $shortcut.WorkingDirectory = $workDir
        $shortcut.Description = "Launch DocuAI Document Intelligence in Browser"
        $shortcut.Save()
        Write-Host "Created shortcut at: $lnkPath"
    }
}
