# PIE backend: the API server (FastAPI) on its own.
#   .\start_backend.ps1              -> http://localhost:8000  (this PC)
#   .\start_backend.ps1 -Network     -> also reachable from other PCs
#   .\start_backend.ps1 -Port 9000
param(
    [int]$Port = 8000,
    [int]$FrontendPort = 5500,
    [switch]$Network
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot    # project root: .env, data/ and outputs/ live here

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Virtual environment not found. Create it once with:" -ForegroundColor Red
    Write-Host "  python -m venv .venv"
    Write-Host "  .\.venv\Scripts\pip install -r backend\requirements.txt"
    exit 1
}
if (-not (Test-Path ".\.env")) {
    Write-Host "Warning: .env not found. Copy .env.example to .env and set Qdrant and OpenAI." -ForegroundColor Yellow
}

# Browsers only let the frontend call the API from origins listed here.
$origins = @("http://localhost:$FrontendPort", "http://127.0.0.1:$FrontendPort", "null")
$bindHost = "127.0.0.1"
$ips = @()
if ($Network) {
    $bindHost = "0.0.0.0"
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254)' } | Select-Object -ExpandProperty IPAddress
    foreach ($ip in $ips) { $origins += "http://${ip}:$FrontendPort" }
}
if (-not $env:PIE_CORS_ORIGINS) { $env:PIE_CORS_ORIGINS = ($origins -join ",") }

Write-Host ""
Write-Host "PIE backend (API) starting" -ForegroundColor Cyan
Write-Host "  API          http://localhost:$Port/"
Write-Host "  API docs     http://localhost:$Port/docs"
foreach ($ip in $ips) { Write-Host "  On network   http://${ip}:$Port/" -ForegroundColor Green }
Write-Host "  Allowed frontends: $env:PIE_CORS_ORIGINS" -ForegroundColor DarkGray
if ($Network) { Write-Host "  Allow inbound TCP $Port in Windows Firewall so other PCs can connect." -ForegroundColor Yellow }
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $python -m uvicorn pie_agent.api:app --app-dir backend --host $bindHost --port $Port
