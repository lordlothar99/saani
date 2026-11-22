---
name: kpis
description: Show the Saani numbers - issues landed per day, conflicts by file, test waits, tokens and estimated cost per issue, role and model - over a window of days, optionally against the previous window. Use when the owner types /saani:kpis or /saani:kpis --baseline.
argument-hint: "[--since date] [--until date] [--baseline]"
---

# /saani:kpis: the numbers

**Arguments:** `$ARGUMENTS`.

1. Run `saani kpis $ARGUMENTS`.
2. Show the numbers, then point out what changed most (with `--baseline`, against the window just before) and the files that conflict
   most.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
