---
name: comment
description: Post a status comment on a GitHub issue with Saani, in the shape of .github/COMMENT_KINDS.md, and set its label. Use when an issue's status changes - blocked, a question for the owner, progress, in review - or when the owner types /saani:comment 42 blocked.
argument-hint: "<issue> <kind> [--text TEXT] [--field KEY=VALUE ...] [--dry-run]"
---

# /saani:comment: a status comment on an issue

**Arguments:** `$ARGUMENTS`. No issue number or kind: ask for them and stop.

1. Kinds: `started`, `review`, `blocked`, `question` (adds the `help wanted` label), `progress`, `on-dev`, `in-pr`, `on-main`, `deployed`,
   `linked`. `start`, `land`, `ci pr` and `ci merged` already post theirs: do not post those by hand.
2. Run `saani comment $ARGUMENTS`. `--field KEY=VALUE` makes a column of the one-row table, `--text` is the body under it, `--dry-run`
   prints the gh commands and posts nothing. Keep it short: tables and checkboxes over prose.
   `review` also records `Reviewed commit` and `Diff hash` of the issue's worktree (`--worktree PATH` names another): the next review reads
   only the commits after it (`saani review diff <n>`).
3. Say which comment was posted and which label changed.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
