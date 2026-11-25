---
name: status
description: Show what the Saani project is doing right now - what runs, what is blocked and on what, what waits, or that everything is idle. Use when the owner types /saani:status.
---

# /saani:status: where things stand

1. Run `saani status` (`saani status --stats` when the owner asks for the landing counts and the test queue's medians).
2. Show its output, then sum it up in a few lines: what runs (and since when), what is blocked and on what, what waits, or that
   everything is idle; tests that run outside the queue are said as such; anything that looks stuck. Say only what the output says.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
