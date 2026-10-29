# Build PIE knowledge for one or more modules, the same way as ESS and Inventory:
# identity -> screen/API mapping -> API/DB mapping -> evidence graph -> Usage catalog (+ Qdrant)
# -> section/field knowledge with AI explanations (+ Qdrant).
#   .\build_modules.ps1 -Modules wms,quality
#   .\build_modules.ps1 -Modules wms -SkipFields        # everything except field knowledge
#   .\build_modules.ps1 -Modules wms -NoEnrich          # field knowledge without AI explanations
# Each step is recorded in the Activity Log; a module's console output goes to outputs/modules/<m>/build.log.
param(
    [Parameter(Mandatory = $true)][string[]]$Modules,
    [switch]$SkipFields,
    [switch]$SkipMapping,
    [switch]$FieldsOnly,      # only section/field knowledge (Phases 14-19 already built)
    [string[]]$Forms,         # fields-build-module: only these forms (e.g. to leave a separately built screen alone)     # Phases 14-15 already done (map-api/map-db --modules batch mode)
    [switch]$NoEnrich,
    [string]$Registry = "outputs/modules/ess/phase13-identity/screen_registry_v13.json",
    [string]$DbSnapshot = "outputs/modules/inventory/phase15-db-mapping/database_object_registry_v15.json"
)
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$Modules = $Modules | ForEach-Object { $_ -split "," } | Where-Object { $_ }
if ($Forms) { $Forms = $Forms | ForEach-Object { $_ -split "," } | Where-Object { $_ } }

foreach ($m in $Modules) {
    $dir = "outputs/modules/$m"
    New-Item -ItemType Directory -Force "$dir/phase13-identity" | Out-Null
    Copy-Item $Registry "$dir/phase13-identity/screen_registry_v13.json" -Force   # the ERP-wide screen registry
    $log = "$dir/build.log"
    "=== $m  $(Get-Date -Format s)" | Out-File $log -Encoding utf8
    $steps = @(
        @("map-api", "--registry", "$dir/phase13-identity/screen_registry_v13.json", "--module", $m, "--output", "$dir/phase14-api-mapping"),
        # Live SQL Server metadata when reachable, else the saved live scan (column types kept).
        @("map-db", "--api-mapping", "$dir/phase14-api-mapping/screen_api_backend_mapping_v14.json", "--output", "$dir/phase15-db-mapping", "--db-snapshot", $DbSnapshot),
        @("build-evidence-graph", "--api-mapping", "$dir/phase14-api-mapping/screen_api_backend_mapping_v14.json",
          "--db-mapping", "$dir/phase15-db-mapping/screen_api_database_mapping_v15.json",
          "--screen-registry", "$dir/phase13-identity/screen_registry_v13.json", "--output", "$dir/phase16-evidence-graph"),
        @("usage-build", "--module", $m),
        @("kms-publish-usage", "--module", $m)
    )
    if ($SkipMapping) { $steps = $steps[2..($steps.Count - 1)] }
    if ($FieldsOnly) { $steps = @() }
    if (-not $SkipFields) {
        $fb = @("fields-build-module", "--module", $m); if (-not $NoEnrich) { $fb += "--enrich" }
        if ($Forms) { $fb += "--forms"; $fb += $Forms }
        $steps += , $fb
        $steps += , @("kms-publish-fields", "--module", $m)
    }
    foreach ($s in $steps) {
        $t0 = Get-Date
        "--- $($s -join ' ')" | Out-File $log -Append -Encoding utf8
        & .\pie.ps1 @s *>> $log
        $ok = $LASTEXITCODE -eq 0
        "{0,-8} {1,-22} {2,-22} {3,6:N0}s" -f $(if ($ok) { "ok" } else { "FAILED" }), $m, $s[0], ((Get-Date) - $t0).TotalSeconds
        if (-not $ok) { "    see $log"; break }
    }
}
