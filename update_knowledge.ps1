# Keep PIE's knowledge in step with GitLab: update the ERP clones, work out which PIE modules the
# new commits touch, and rebuild only those.
#   .\update_knowledge.ps1 -Check                 -> show new commits and affected modules; change nothing
#   .\update_knowledge.ps1                        -> sync, code index, rebuild affected modules
#   .\update_knowledge.ps1 -NoEnrich              -> rebuild without AI field explanations (no OpenAI cost there)
#   .\update_knowledge.ps1 -Modules inventory     -> rebuild these modules (after syncing), whatever changed
#   .\update_knowledge.ps1 -RegisterTask -At 02:00 -> run this every night at 02:00 (Windows Task Scheduler)
#
# How a changed file is matched to a module:
#   frontend  projects/<module>/...                    -> that module (same names as outputs/modules)
#   backend   a file (or its folder) referenced by a module's screen -> API mapping (phase14) -> that module
# Changes used by more than -SharedLimit modules (framework, shared libraries) are reported but do not
# trigger rebuilds, and more than -MaxModules affected modules are reported instead of rebuilt, to keep
# OpenAI cost and run time predictable. Brand-new screens are reported: they need ERP discovery first
# (see the end of the report). Every run is saved to outputs/logs/update_knowledge_<time>.log.
param(
    [switch]$Check,
    [switch]$NoEnrich,
    [string[]]$Modules,
    [switch]$SkipCodeIndex,
    [int]$MaxModules = 12,
    [int]$SharedLimit = 3,
    [switch]$RegisterTask,
    [string]$At = "02:00"
)
Set-Location $PSScriptRoot

if ($RegisterTask) {
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\update_knowledge.ps1`""
    if ($NoEnrich) { $cmd += " -NoEnrich" }
    schtasks /Create /F /SC DAILY /ST $At /TN "PIE update knowledge" /TR $cmd | Out-Host
    Write-Host "Scheduled daily at $At as 'PIE update knowledge'. Remove with: schtasks /Delete /TN `"PIE update knowledge`" /F"
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force "outputs/logs" | Out-Null
$logFile = "outputs/logs/update_knowledge_$(Get-Date -Format yyyyMMdd_HHmmss).log"
Start-Transcript -Path $logFile | Out-Null

$repos = @(
    @{ Name = "frontend"; Path = "data/erp/frontend" },
    @{ Name = "backend";  Path = "data/erp/backend" }
)
$known = @(Get-ChildItem "outputs/modules" -Directory | ForEach-Object { $_.Name })

# ---- 1. The commit each repository's knowledge was last built from (first run: where the clone
# is now), then sync (or just fetch with -Check). Manual syncs in between are still covered.
$stateFile = "outputs/logs/knowledge_state.json"
$state = @{}
if (Test-Path $stateFile) {
    $saved = Get-Content $stateFile -Raw | ConvertFrom-Json
    foreach ($p in $saved.PSObject.Properties) { $state[$p.Name] = $p.Value }
}
$before = @{}
foreach ($r in $repos) {
    if (-not (Test-Path (Join-Path $r.Path ".git"))) { continue }
    $from = $state[$r.Name]
    if ($from) { & git -C $r.Path cat-file -e "$from^{commit}" 2>$null; if ($LASTEXITCODE -ne 0) { $from = $null } }
    if (-not $from) { $from = (& git -C $r.Path rev-parse HEAD) }
    $before[$r.Name] = $from
}
if ($Check) { & .\sync_erp.ps1 -Check } else { & .\sync_erp.ps1 }

# ---- 2. Files changed by the new commits.
$changed = @{}
$added = @{}
foreach ($r in $repos) {
    $from = $before[$r.Name]
    if (-not $from) { continue }
    $branch = (& git -C $r.Path branch --show-current)
    $to = if ($Check) { "origin/$branch" } else { "HEAD" }
    $changed[$r.Name] = @(& git -C $r.Path diff --name-only "$from..$to")
    $added[$r.Name] = @(& git -C $r.Path diff --name-only --diff-filter=A "$from..$to")
}

# ---- 3. Backend file -> modules, from each module's screen -> API mapping.
$fileModules = @{}
$dirModules = @{}
foreach ($m in $known) {
    $map = "outputs/modules/$m/phase14-api-mapping/screen_api_backend_mapping_v14.json"
    if (-not (Test-Path $map)) { continue }
    $text = [IO.File]::ReadAllText((Resolve-Path $map))
    foreach ($x in [regex]::Matches($text, '"((?:GB5Solution|GB5Framework|GB5Shared)/[^"]+?\.cs)"')) {
        $f = $x.Groups[1].Value
        $d = $f.Substring(0, $f.LastIndexOf("/"))
        if (-not $fileModules[$f]) { $fileModules[$f] = New-Object System.Collections.Generic.HashSet[string] }
        if (-not $dirModules[$d]) { $dirModules[$d] = New-Object System.Collections.Generic.HashSet[string] }
        [void]$fileModules[$f].Add($m)
        [void]$dirModules[$d].Add($m)
    }
}

$affected = New-Object System.Collections.Generic.SortedSet[string]
$shared = New-Object System.Collections.Generic.List[string]
$unmatched = New-Object System.Collections.Generic.List[string]
function Add-Hit([string]$file, $mods) {
    $list = @($mods)
    if ($list.Count -gt $SharedLimit) { $shared.Add("$file  (used by $($list.Count) modules)"); return }
    foreach ($m in $list) { [void]$affected.Add($m) }
}
foreach ($f in $changed["frontend"]) {
    if ($f -match '^projects/([^/]+)/' -and $known -contains $Matches[1]) { Add-Hit $f @($Matches[1]) }
    elseif ($f -match '^(libs|features|projects/gbhost)/') { $shared.Add("frontend/$f") }
    else { $unmatched.Add("frontend/$f") }
}
foreach ($f in $changed["backend"]) {
    if (-not $f.EndsWith(".cs")) { $unmatched.Add("backend/$f"); continue }
    $d = $f.Substring(0, [Math]::Max(0, $f.LastIndexOf("/")))
    if ($fileModules[$f]) { Add-Hit "backend/$f" $fileModules[$f] }
    elseif ($dirModules[$d]) { Add-Hit "backend/$f" $dirModules[$d] }
    else { $unmatched.Add("backend/$f") }
}
$newScreens = @($added["frontend"] | Where-Object { $_ -match '^projects/[^/]+/.+\.component\.ts$' -and $_ -notmatch '\.spec\.ts$' })

# ---- 4. Report.
$total = @($changed.Values | ForEach-Object { $_ }).Count
Write-Host ""
Write-Host "== Changes since the last update: $total file(s)" -ForegroundColor Cyan
if ($affected.Count) { Write-Host "  Modules affected: $($affected -join ', ')" -ForegroundColor Green }
else { Write-Host "  No module-specific changes." }
if ($shared.Count) {
    Write-Host "  Shared code changed ($($shared.Count) file(s), not rebuilt automatically):" -ForegroundColor Yellow
    $shared | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" }
}
if ($unmatched.Count) { Write-Host "  Not linked to a module (code index only): $($unmatched.Count) file(s)" -ForegroundColor DarkGray }
if ($newScreens.Count) {
    Write-Host "  New screens added ($($newScreens.Count)); PIE needs ERP discovery before it can describe them:" -ForegroundColor Yellow
    $newScreens | Select-Object -First 10 | ForEach-Object { Write-Host "    frontend/$_" }
}

$targets = @()
if ($Modules) { $targets = @($Modules | ForEach-Object { $_ -split "," } | Where-Object { $_ }) }
else { $targets = @($affected) }

if ($Check) {
    Write-Host ""
    Write-Host "Check only: nothing was updated or rebuilt." -ForegroundColor DarkGray
    Stop-Transcript | Out-Null
    exit 0
}

# ---- 5. Rebuild.
if ($total -gt 0 -and -not $SkipCodeIndex) {
    Write-Host ""
    Write-Host "== Code index (changed files only)" -ForegroundColor Cyan
    & .\pie.ps1 code-index
}
if ($targets.Count -gt $MaxModules -and -not $Modules) {
    Write-Host ""
    Write-Host "$($targets.Count) modules affected, more than -MaxModules ${MaxModules}: not rebuilt automatically. Run:" -ForegroundColor Yellow
    Write-Host "  .\build_modules.ps1 -Modules $($targets -join ',')"
} elseif ($targets.Count) {
    Write-Host ""
    Write-Host "== Rebuilding: $($targets -join ', ')" -ForegroundColor Cyan
    if ($NoEnrich) { & .\build_modules.ps1 -Modules $targets -NoEnrich } else { & .\build_modules.ps1 -Modules $targets }
}

# Next run starts from here (modules left for a manual rebuild above are reported in this log).
$now = @{}
foreach ($r in $repos) { if ($before[$r.Name]) { $now[$r.Name] = (& git -C $r.Path rev-parse HEAD) } }
$now | ConvertTo-Json | Set-Content $stateFile -Encoding utf8

Write-Host ""
if ($newScreens.Count) {
    Write-Host "New screens: to add them, refresh the ERP screen list, then rebuild their modules:" -ForegroundColor Yellow
    Write-Host "  .\pie.ps1 discover-erp"
    Write-Host "  .\pie.ps1 screen-identity --input outputs/phase12-discovery/erp_discovery_report.json"
    Write-Host "  .\build_modules.ps1 -Modules <module> -Registry outputs/phase13-identity/screen_registry_v13.json"
}
Write-Host "Qdrant is shared with the server, so published knowledge is live there now. Copy rebuilt outputs/modules/<module> folders to the server for the catalog and field pages." -ForegroundColor DarkGray
Write-Host "Log: $logFile" -ForegroundColor DarkGray
Stop-Transcript | Out-Null
