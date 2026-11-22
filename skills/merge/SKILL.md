---
name: merge
description: Merge the green integration -> production pull request, then relabel and clean up; optionally deploy. Use when the owner types /saani:merge or /saani:merge --deploy, or says "merge" or "merge and deploy" in their own words.
argument-hint: "[PR] [--deploy] [--dry-run]"
disable-model-invocation: true
---

# /saani:merge: merge the pull request that an auto run left

**Arguments:** `$ARGUMENTS`.

The owner saying "merge" or "merge and deploy" in their own words in a session means this command: `saani merge`, and `saani merge --deploy`.
Only the owner starts it; an auto run never does.

1. Run `saani merge $ARGUMENTS`. It finds the one open integration -> production pull request (or the number given), refuses unless it is
   open, mergeable and its CI is green on the exact head (the `[ci] status_context` status and every check run), merges it with a merge commit
   (the trailers stay; the integration branch is never deleted), then does what `saani ci merged` does: relabel the issues, remove the
   finished auto runs. `--dry-run` runs the checks and prints what would happen, merging nothing.
2. `--deploy`: after the merge, `saani deploy` (the checks, the deploy, the log, the prod tag, the issues; `/saani:deploy`). If it refuses
   or fails, the merge stands; say that, and what the deploy's message says to do.
3. Merged: say the pull request, that the issues were relabelled and which auto runs were removed. Refused: show the reason and the step it
   names (usually `saani ci pr`); never merge by another route, never force, never weaken a check.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
