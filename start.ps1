# Start backend (:8000) and dashboard (:3000) in the background. Logs go to data/*.log
$root = $PSScriptRoot
Get-NetTCPConnection -LocalPort 8000,3000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Process -WindowStyle Hidden -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","backend.main:app","--port","8000" -WorkingDirectory $root -RedirectStandardOutput "$root\data\backend.log" -RedirectStandardError "$root\data\backend.err.log"
Start-Process -WindowStyle Hidden -FilePath "cmd.exe" -ArgumentList "/c","npm run dev" -WorkingDirectory "$root\dashboard" -RedirectStandardOutput "$root\data\next.log" -RedirectStandardError "$root\data\next.err.log"
Write-Host "started: backend http://localhost:8000  dashboard http://localhost:3000"
