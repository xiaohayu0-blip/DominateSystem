param([switch]$Initialize)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
$webRoot = Join-Path $projectRoot 'web'
if (-not (Test-Path -LiteralPath (Join-Path $backendRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.demo.example') -Destination (Join-Path $backendRoot '.env')
}
Push-Location $backendRoot
try {
    uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & '.\.venv\Scripts\python.exe' -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
    if ($Initialize) {
        & '.\.venv\Scripts\python.exe' -m app.cli demo-init
        if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed; existing data was preserved.' }
    }
    $pythonExe = Join-Path $backendRoot '.venv\Scripts\python.exe'
    $serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log') -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru
    $workerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','app.alerts') -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru
    Set-Location $webRoot
    npm ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed' }
    Write-Host 'Website: http://localhost:5173 ; Admin: http://localhost:5173/#/admin'
    Write-Host 'Demo credentials: backend/runtime/demo-access.txt. Ctrl+C stops the website and child processes.'
    npm run dev
} finally {
    if ($serverProcess -and -not $serverProcess.HasExited) { Stop-Process -Id $serverProcess.Id }
    if ($workerProcess -and -not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id }
    Pop-Location
}
