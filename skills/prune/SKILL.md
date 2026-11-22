---
name: prune
description: Delete the merged branches of the Saani project, locally and on GitHub (never the integration or production branch, long-run branches or checked-out ones). Use when the owner types /saani:prune.
argument-hint: "[--local-only]"
disable-model-invocation: true
---

# /saani:prune: delete merged branches

**Arguments:** `$ARGUMENTS` (`--local-only` leaves GitHub alone).

1. Run `saani prune --dry-run $ARGUMENTS` and show the list of branches it would delete.
2. Nothing to delete: say so and stop. Otherwise ask the owner to confirm; deleted branches are gone from GitHub too.
3. On a yes, run `saani prune $ARGUMENTS` and say what was deleted.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
