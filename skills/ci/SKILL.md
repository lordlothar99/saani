---
name: ci
description: Saani hybrid CI - GitHub Actions within the free minutes, the PC for the rest. Use when the owner types /saani:ci, /saani:ci status, /saani:ci pr, /saani:ci sync, /saani:ci place <job> or /saani:ci merged <pr>.
argument-hint: "[status|sync|pr|place <job>|merged <pr>] [--dry-run]"
---

# /saani:ci: hybrid CI

**Arguments:** `$ARGUMENTS`. Empty: `status`.

| Sub-command | What it does |
|---|---|
| `status` | this month's GitHub Actions minutes, the reserve, and where each job would run now |
| `sync` | switches the small automatic workflows on or off by the minutes left |
| `pr` | the CI of the open integration -> production pull request, on GitHub or on the PC; the result becomes a commit status (`--head <branch>` for a hotfix branch) |
| `place <job>` | prints `github` or `pc` for one job, and why |
| `merged <pr>` | after a merge into production: relabels the issues (on GitHub, or here if GitHub did not) and removes the finished long runs |

`--dry-run` prints every write (dispatch, status, workflow switch) instead of doing it.

1. Run `saani ci $ARGUMENTS` (or `saani ci status`). `pr` can take a while: start it in the background.
2. Report the outcome. Red: say which job failed and why; never weaken a test to turn it green.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
