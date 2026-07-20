# PostToolUse hook: mirror .claude/skills -> .agents/skills after a skill file is
# written or edited, so both harnesses share the same reviewed skills. Reads the
# hook payload (JSON) on stdin; only mirrors when the touched file is under
# .claude/skills. Registered in .claude/settings.json.
$j = [Console]::In.ReadToEnd() | ConvertFrom-Json
$f = [string]$j.tool_input.file_path
if ($f -match '\.claude[\\/]skills[\\/]') {
    $r = $env:CLAUDE_PROJECT_DIR
    if (-not $r) { $r = (Get-Location).Path }
    # /MIR makes .claude/skills the sole authority: anything present only in
    # .agents/skills is DELETED on the next skill edit. Never author a skill directly
    # under .agents/skills — it will not survive. Add it to .claude/skills instead.
    robocopy "$r\.claude\skills" "$r\.agents\skills" /MIR /NJH /NJS /NP /NFL /NDL | Out-Null
    # robocopy exit codes 0-7 are success; 8+ is a real failure.
    if ($LASTEXITCODE -ge 8) { exit 1 }
}
exit 0
