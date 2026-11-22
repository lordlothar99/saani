---
name: progress
description: Follow a Saani background test run - a progress bar, failures so far and the time left. Use when the owner types /saani:progress or asks how far a test run is.
argument-hint: "[file]"
---

# /saani:progress: how far a test run is

**Arguments:** `$ARGUMENTS`: the output file of the run. Empty: the most recent file a `saani progress run --out <file>` wrote in this
session; none known, ask for it.

1. Run `saani progress show <file>`: one line with the bar, done/total, failures and the time left.
2. To follow it until the end, run `saani progress watch <file> --every 180` in the background and report each line.
3. To start a run that can be followed: `saani progress run --out <file> -- <test command>`, in the background.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
