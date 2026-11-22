---
name: test
description: Run tests through the Saani machine-wide test queue, one job at a time. Use when the owner types /saani:test, /saani:test full, or /saani:test -- <command>.
argument-hint: "[tier1|land|full] [-- command]"
---

# /saani:test: a run through the test queue

**Arguments:** `$ARGUMENTS`.

1. Pick the kind: `tier1`, `land` or `full` if one is given, else `tier1`.
2. Pick the command: the one after `--` if given, else the project's own from saani.toml (`[tests] tier1`, or `[tests] full` for
   `full`), with `{python}` replaced by the Python running Saani. Neither: say so and stop.
3. Run `saani test --kind <kind> -- <command>`. A slow run (`full`): start it in the background and say how to follow it (`/saani:progress`).
4. Report the result: passed, or the failures and what they point to.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
