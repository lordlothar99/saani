---
name: test
description: Run tests through the Saani machine-wide test queue, one job at a time. Use when the owner types /saani:test, /saani:test full, or /saani:test -- <command>.
argument-hint: "[landing|full] [-- command]"
---

# /saani:test: a run through the test queue

**Arguments:** `$ARGUMENTS`.

1. Pick the kind: `landing` or `full` if one is given, else `landing` (`tier1` is the old name of `landing`; `land` is only for
   `saani land`'s own run, never typed).
2. Pick the command: the one after `--` if given, else the project's own from saani.toml (`[tests] landing`, or `[tests] full` for
   `full`), with `{python}` replaced by the Python running Saani. Neither: say so and stop.
3. Run `saani test --kind <kind> -- <command>`. A slow run (`full`): start it in the background and say how to follow it (`/saani:progress`).
4. Report the result: passed, or the failures and what they point to. A line `reused: green on <tree> ...` means the queue took a stored
   green result of the same tree and command instead of running (clean checkout only); say so, and run `saani test --fresh ...` only if
   the owner wants a real run.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
