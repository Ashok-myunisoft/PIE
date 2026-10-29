# PIE frontend: the web pages on their own (static files, no build step).
#   .\start_frontend.ps1                                   -> http://localhost:5500, backend at http://localhost:8000
#   .\start_frontend.ps1 -Backend http://192.168.0.90:8000 -> point the pages at another backend
#   .\start_frontend.ps1 -Network                          -> also reachable from other PCs
param(
    [int]$Port = 5500,
    [string]$Backend = "",
    [switch]$Network
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

# The only link between frontend and backend is frontend\config.js.
if ($Backend) {
    $api = $Backend.TrimEnd("/")
    if ($api -notmatch "/usage$") { $api = "$api/usage" }
    $config = @"
// PIE frontend configuration.
// apiBase = where the PIE backend API runs. Change it when the backend is on another
// machine or port, e.g. "http://192.168.0.90:8000/usage/". Keep the trailing /usage/.
window.PIE_CONFIG = window.PIE_CONFIG || {
  apiBase: "$api/"
};
"@
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "frontend\config.js"), $config, (New-Object System.Text.UTF8Encoding($false)))
}
$current = (Select-String -Path "frontend\config.js" -Pattern 'apiBase:\s*"([^"]+)"').Matches.Groups[1].Value

$bindHost = if ($Network) { "0.0.0.0" } else { "127.0.0.1" }
Write-Host ""
Write-Host "PIE frontend (web pages) starting" -ForegroundColor Cyan
Write-Host "  Home             http://localhost:$Port/"
Write-Host "  Ask PIE          http://localhost:$Port/chat.html"
Write-Host "  Feature Catalog  http://localhost:$Port/catalog.html"
Write-Host "  Field Explorer   http://localhost:$Port/fields.html"
Write-Host "  Talks to backend $current" -ForegroundColor DarkGray
if ($Network) {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254)' } |
        ForEach-Object { Write-Host "  On network       http://$($_.IPAddress):$Port/" -ForegroundColor Green }
    Write-Host "  For other PCs, the backend address in config.js must be reachable by them (not localhost)." -ForegroundColor Yellow
}
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $python -m http.server $Port --bind $bindHost --directory frontend
