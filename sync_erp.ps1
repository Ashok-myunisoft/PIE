# Bring the ERP source PIE reads (data/erp/frontend and data/erp/backend) up to date with GitLab.
#   .\sync_erp.ps1                 -> fetch both, show what is new, update (fast-forward only)
#   .\sync_erp.ps1 -Check          -> fetch and show what is new, change no files
#   .\sync_erp.ps1 -Only backend   -> just one repository
#
# Repository, branch, token and folder come from .env (ERP_FRONTEND_REPO / _BRANCH / _TOKEN /
# ERP_FRONTEND_PATH, and the same for BACKEND). The token is used only while git runs: it is not
# written to .git/config or printed. A missing folder is cloned. A repository with local edits is
# skipped, and an update that is not a clean fast-forward is refused, so nothing is overwritten.
# The clones are read-only input for PIE: change ERP code in GitLab, not here.
param(
    [switch]$Check,
    [ValidateSet("frontend", "backend")]
    [string]$Only = ""
)
Set-Location $PSScriptRoot

function Read-DotEnv([string]$path) {
    $vars = @{}
    if (-not (Test-Path $path)) { return $vars }
    foreach ($line in Get-Content $path) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
        $k, $v = $t.Split("=", 2)
        $vars[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
    }
    return $vars
}

# Runs git with the token as an HTTP header for this call only (never saved, never on the command line).
function Invoke-GitWithToken([string]$token, [string[]]$gitArgs) {
    $auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("oauth2:$token"))
    $env:GIT_CONFIG_COUNT = "1"
    $env:GIT_CONFIG_KEY_0 = "http.extraHeader"
    $env:GIT_CONFIG_VALUE_0 = "Authorization: Basic $auth"
    $env:GIT_TERMINAL_PROMPT = "0"
    try {
        & git @gitArgs
        return $LASTEXITCODE
    } finally {
        Remove-Item Env:GIT_CONFIG_COUNT, Env:GIT_CONFIG_KEY_0, Env:GIT_CONFIG_VALUE_0, Env:GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue
    }
}

$envVars = Read-DotEnv ".env"
if (-not $envVars.Count) {
    Write-Host ".env not found. Copy .env.example to .env and set the ERP_* repository settings." -ForegroundColor Red
    exit 1
}

$targets = @("frontend", "backend")
if ($Only) { $targets = @($Only) }
$failed = 0

foreach ($name in $targets) {
    $key = $name.ToUpper()
    $repo = $envVars["ERP_${key}_REPO"]
    $branch = $envVars["ERP_${key}_BRANCH"]
    $token = $envVars["ERP_${key}_TOKEN"]
    $path = $envVars["ERP_${key}_PATH"]
    if (-not $path) { $path = "./data/erp/$name" }

    Write-Host ""
    Write-Host "== ERP $name  ($path, branch $branch)" -ForegroundColor Cyan
    if (-not $repo -or -not $branch -or -not $token) {
        Write-Host "  Skipped: set ERP_${key}_REPO, ERP_${key}_BRANCH and ERP_${key}_TOKEN in .env." -ForegroundColor Yellow
        $failed++; continue
    }

    # First time: clone.
    if (-not (Test-Path (Join-Path $path ".git"))) {
        if (Test-Path $path) {
            Write-Host "  Skipped: $path exists but is not a git repository. Move it away, then run again." -ForegroundColor Yellow
            $failed++; continue
        }
        if ($Check) { Write-Host "  Not cloned yet (run without -Check to clone)."; continue }
        Write-Host "  Cloning $repo ..."
        if ((Invoke-GitWithToken $token @("clone", "--branch", $branch, $repo, $path)) -ne 0) {
            Write-Host "  Clone failed: check the repository address, branch and token in .env." -ForegroundColor Red
            $failed++
        } else {
            Write-Host "  Cloned." -ForegroundColor Green
        }
        continue
    }

    # Safety checks: right repository, right branch, no local edits.
    $origin = (& git -C $path remote get-url origin)
    if ($origin -ne $repo) {
        Write-Host "  Skipped: origin is $origin, but .env says $repo." -ForegroundColor Yellow
        $failed++; continue
    }
    $current = (& git -C $path branch --show-current)
    if ($current -ne $branch) {
        Write-Host "  Skipped: the clone is on branch '$current', .env says '$branch'. Run: git -C $path switch $branch" -ForegroundColor Yellow
        $failed++; continue
    }
    $dirty = & git -C $path status --porcelain
    if ($dirty) {
        Write-Host "  Skipped: there are local changes (PIE only reads this code). Review them with:" -ForegroundColor Yellow
        Write-Host "    git -C $path status      then keep them aside with: git -C $path stash"
        $failed++; continue
    }

    # Download new commits; no files change yet.
    if ((Invoke-GitWithToken $token @("-C", $path, "fetch", "--quiet", "origin", $branch)) -ne 0) {
        Write-Host "  Fetch failed: check the network and the token in .env." -ForegroundColor Red
        $failed++; continue
    }
    $ahead = [int](& git -C $path rev-list --count "HEAD..origin/$branch")
    $local = [int](& git -C $path rev-list --count "origin/$branch..HEAD")
    if ($ahead -eq 0) {
        Write-Host "  Up to date ($(& git -C $path log -1 --format='%h %ad' --date=short))." -ForegroundColor Green
        continue
    }
    Write-Host "  $ahead new commit(s) in GitLab:"
    & git -C $path log --no-merges --format="    %h %ad %an: %s" --date=short -n 15 "HEAD..origin/$branch"
    if ($ahead -gt 15) { Write-Host "    ..." }
    Write-Host "  $(& git -C $path diff --shortstat "HEAD..origin/$branch")"

    if ($Check) { Write-Host "  Not updated (-Check)." -ForegroundColor DarkGray; continue }
    if ($local -gt 0) {
        Write-Host "  Not updated: this clone has $local local commit(s) that are not in GitLab. Review with: git -C $path log origin/$branch..HEAD" -ForegroundColor Yellow
        $failed++; continue
    }
    & git -C $path merge --ff-only --quiet "origin/$branch"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Update refused (not a clean fast-forward). Nothing was changed." -ForegroundColor Red
        $failed++
    } else {
        Write-Host "  Updated to $(& git -C $path log -1 --format='%h %ad' --date=short)." -ForegroundColor Green
    }
}

Write-Host ""
if (-not $Check) {
    Write-Host "Next: rebuild PIE knowledge for the changed modules, e.g. .\pie.ps1 fields-build --enrich and .\pie.ps1 usage-build --module <module>." -ForegroundColor DarkGray
}
exit $failed
