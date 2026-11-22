---
name: ui
description: Open the Saani UI - the board, the local read-only status page of the project (issues, worktrees, test queue, Claude sessions). Use when the owner types /saani:ui, or says "show me the UI", "open the board" or asks where things stand at a glance.
argument-hint: "[--port N]"
---

# /saani:ui: the board

**Arguments:** `$ARGUMENTS` (optional, e.g. `--port 8772`).

1. Start `saani ui --open $ARGUMENTS` **in the background** (it is a server and keeps running). It opens the board at
   `http://127.0.0.1:8771/board` (or the port given) in the browser; only this machine can reach it.
2. If the port is already taken, the board is probably already running: give the owner the URL instead of starting a second one.
3. Say how to stop it: ask Claude, or close the session. To answer a question about the state without a browser, `saani ui --json`
   prints it once.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
