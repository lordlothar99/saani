---
name: new
description: Start work on a GitHub issue with Saani - its own git worktree and branch from the integration branch. Use when the owner types /saani:new 42 or /saani:new 42 --hotfix.
argument-hint: "<issue> [--hotfix] [--slug words] [--no-assign]"
disable-model-invocation: true
---

# /saani:new: a worktree for an issue

**Arguments:** `$ARGUMENTS`. No issue number: ask for it and stop.

1. Run `saani new $ARGUMENTS`. `--hotfix` starts from the production tag and lands later as a pull request into production;
   `--slug` sets the branch words; `--no-assign` leaves the issue unassigned.
2. Say the worktree folder and the branch it created, and offer to work on the issue there.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
