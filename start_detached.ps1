$pyPath = "C:\Users\risha\AppData\Local\Programs\Python\Python313\python.exe"
$workDir = "C:\Users\risha\.gemini\antigravity\scratch\rag--system"

# Kill any existing process on port 8000
$conns = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
}

# Start uvicorn detached with hidden window
Start-Process -FilePath $pyPath -ArgumentList "-m uvicorn app.main:app --host 0.0.0.0 --port 8000" -WorkingDirectory $workDir -WindowStyle Hidden

Start-Sleep -Seconds 3
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
