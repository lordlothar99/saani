---
name: review
description: Show the diff a review of a Saani issue must read - only the commits after the issue's last recorded review, or the whole diff. Use when re-reviewing a worktree after fixes, or when the owner types /saani:review diff 42.
argument-hint: "diff <issue> [--worktree PATH] [--base REF]"
---

# /saani:review: the diff a review reads

**Arguments:** `$ARGUMENTS`. No `diff` and issue number: ask for them and stop.

1. Run `saani review $ARGUMENTS`. It reads the issue's `In review` comments for the last `Reviewed commit` (`saani comment <n> review`
   records it with the `Diff hash`) and prints the range it covers, then the diff: the commits after that commit, or the whole diff when
   nothing is recorded or the commit is no longer in the branch. Files matching `[review] always_whole` (guard, landing, CI, merge,
   deploy code by default) are printed whole. `--base REF` changes what the whole diff is taken against (default `origin/<integration>`).
   With no worktree for the issue it refuses: pass `--worktree PATH`.
2. Say which range it covered and what was not re-read.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
