# Mirrors the canonical agent-config/ sources into the tool-specific
# directories each agent harness actually reads (.claude/, .agents/).
# Those directories are wholly gitignored - always edit under agent-config/
# and rerun this script (or let the PostToolUse hook rerun it for you).
param(
    [string]$ProjectRoot = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'
$src = Join-Path $ProjectRoot 'agent-config'

function Mirror-Dir($from, $to) {
    robocopy $from $to /MIR /NJH /NJS /NP /NFL /NDL | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $from -> $to (exit $LASTEXITCODE)" }
}

# robocopy's own exit codes (0-7 success, 8+ failure) leak into $LASTEXITCODE
# and would otherwise look like this script failed to a caller that checks it
# (e.g. the PostToolUse hook) even on a fully successful run; force a clean
# 0/1 result instead.
try {
    Mirror-Dir (Join-Path $src 'skills') (Join-Path $ProjectRoot '.claude\skills')
    Mirror-Dir (Join-Path $src 'skills') (Join-Path $ProjectRoot '.agents\skills')
    Mirror-Dir (Join-Path $src 'claude\hooks') (Join-Path $ProjectRoot '.claude\hooks')

    $settingsDst = Join-Path $ProjectRoot '.claude\settings.json'
    New-Item -ItemType Directory -Force -Path (Split-Path $settingsDst) | Out-Null
    Copy-Item (Join-Path $src 'claude\settings.json') $settingsDst -Force
} catch {
    Write-Error $_
    exit 1
}
exit 0
