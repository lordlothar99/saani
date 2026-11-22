---
name: status
description: Show where the Saani project stands right now - the landing lock, the worktrees, the conflicts per landing, the test queue. Use when the owner types /saani:status.
---

# /saani:status: where things stand

1. Run `saani status`.
2. Show its output, then sum it up in a few lines: is the landing lock held (by whom, since when), which worktrees exist and for which
   issues, what is queued or running in the test queue, and anything that looks stuck.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
