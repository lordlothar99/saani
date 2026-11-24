---
name: land
description: Land the current Saani worktree into the integration branch - lock, merge, rebuild generated files, landing tests, push, clean up. Use when the owner types /saani:land or /saani:land --dry-run.
argument-hint: "[--dry-run] [--keep] [--wait minutes] [--worktree path] [--no-comment]"
disable-model-invocation: true
---

# /saani:land: land a worktree

**Arguments:** `$ARGUMENTS`.

1. Check you are in an issue's worktree (or that `--worktree` names one) and that its changes are committed; if not, say so and stop.
2. Run `saani land $ARGUMENTS`. `--dry-run` prints the plan and changes nothing; `--keep` keeps the worktree and branch afterwards;
   `--wait` sets how many minutes to wait for the landing lock; `--no-comment` prints the `On dev` comment and label step instead of
   doing it.
3. Landed: say the commit and that the worktree was cleaned up (unless `--keep`). Refused or failed: show why (a conflict, a red test,
   the lock); never force, never weaken a test to get through.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
