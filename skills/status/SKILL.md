---
name: status
description: Show what the Saani project's machinery is doing right now - what runs, what is blocked and on what, what waits, or that nothing does. Use when the owner types /saani:status.
---

# /saani:status: where things stand

1. Run `saani status` (`saani status --stats` when the owner asks for the landing counts and the test queue's medians).
2. Show its output, then sum it up in a few lines: what runs (and since when), what is blocked and on what, what waits, or that there is
   no machinery activity; tests that run outside the queue are said as such; anything that looks stuck. Say only what the output says:
   Saani cannot see roles (Architect, coder, tester, reviewer) or what a Claude session is thinking or editing, so never say that an
   agent is idle, only that the landing, the tests and the auto run are. The output shows the issue of a worktree and the kind of test
   run instead of a role.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
