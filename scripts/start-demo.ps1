param(
    [switch]$Initialize,
    [string]$LanAddress
)
$ErrorActionPreference = 'Stop'
$originBeforeStart = $env:DEMO_LAN_ORIGIN
$webHost = '127.0.0.1'
$webUrl = 'http://localhost:5173'
if ($LanAddress) {
    $address = $null
    if ($LanAddress -notmatch '^(?:\d{1,3}\.){3}\d{1,3}$' -or
        -not [System.Net.IPAddress]::TryParse($LanAddress, [ref]$address)) {
        throw 'LanAddress must be the IPv4 address of this computer.'
    }
    $bytes = $address.GetAddressBytes()
    if (-not ($bytes[0] -eq 10 -or ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
              ($bytes[0] -eq 192 -and $bytes[1] -eq 168))) {
        throw 'LanAddress must be a private IPv4 address.'
    }
    $webHost = $address.ToString()
    $webUrl = "http://${webHost}:5173"
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
$webRoot = Join-Path $projectRoot 'web'
if (-not (Test-Path -LiteralPath (Join-Path $backendRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.demo.example') -Destination (Join-Path $backendRoot '.env')
}
Push-Location $backendRoot
try {
    $bgeEnabled = $env:EMBEDDING_MODE -eq 'bge' -or (Select-String -LiteralPath '.env' -Pattern '^\s*EMBEDDING_MODE\s*=\s*bge\s*$' -Quiet)
    if ($bgeEnabled) {
        uv sync --locked --extra bge
    } else {
        uv sync --locked
    }
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & '.\.venv\Scripts\python.exe' -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
    if ($Initialize) {
        & '.\.venv\Scripts\python.exe' -m app.cli demo-init
        if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed; existing data was preserved.' }
    }
    $pythonExe = Join-Path $backendRoot '.venv\Scripts\python.exe'
    if ($LanAddress) {
        & $pythonExe -c "from app.config import settings; import sys; sys.exit(settings().environment != 'demo')"
        if ($LASTEXITCODE -ne 0) { throw 'LAN startup is available only in the demo environment.' }
        $env:DEMO_LAN_ORIGIN = $webUrl
    }
    $serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log') -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru
    $workerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','app.alerts') -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru
    Set-Location $webRoot
    npm ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed' }
    Write-Host "Website: $webUrl/ ; Admin: $webUrl/#/admin"
    Write-Host 'Demo credentials: backend/runtime/demo-access.txt. Ctrl+C stops the website and child processes.'
    npm run dev -- --host $webHost --port 5173 --strictPort
} finally {
    if ($serverProcess -and -not $serverProcess.HasExited) { Stop-Process -Id $serverProcess.Id }
    if ($workerProcess -and -not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id }
    $env:DEMO_LAN_ORIGIN = $originBeforeStart
    Pop-Location
}
