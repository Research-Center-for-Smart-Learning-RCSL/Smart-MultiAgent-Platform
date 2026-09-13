# PostToolUse hook: whenever a skill/settings/hook file under agent-config/ is
# written or edited, resync the generated .claude/ and .agents/ mirrors so both
# harnesses immediately see the change. Reads the hook payload (JSON) on stdin.
# Registered in agent-config/claude/settings.json (copied to .claude/settings.json).
$j = [Console]::In.ReadToEnd() | ConvertFrom-Json
$f = [string]$j.tool_input.file_path
if ($f -match '[\\/]agent-config[\\/]') {
    $r = $env:CLAUDE_PROJECT_DIR
    if (-not $r) { $r = (Get-Location).Path }
    & "$r\agent-config\sync.ps1" -ProjectRoot $r
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
exit 0
