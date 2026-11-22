---
name: projects
description: List, add or remove the projects the Saani board offers in its project selector. Use when the owner types /saani:projects, /saani:projects add <folder>, /saani:projects remove <owner/name>, or asks to show another project in the board.
argument-hint: "[list | add [FOLDER] | remove OWNER/NAME]"
---

# /saani:projects: the board's project list

**Arguments:** `$ARGUMENTS` (empty: `list`).

Run `saani projects $ARGUMENTS`. The list is `~/.saani/projects.toml` (the path of each project's checkout, where its `saani.toml` is); the
project `saani ui` starts in is added to it by itself. `add` without a folder adds the project of the current folder. The board reads the
list at every request: a running board shows a change at its next refresh, no restart.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
