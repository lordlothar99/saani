---
name: release
description: Write the release notes from the GitHub issues of a milestone, or of a pull request, grouped by issue Type with each issue's Result line. Use when the owner types /saani:release notes <milestone> or /saani:release notes --pr <N>.
disable-model-invocation: true
argument-hint: "notes <milestone> | notes --pr <N> [--post]"
---

# /saani:release: the release notes

**Arguments:** `$ARGUMENTS`.

1. Run `saani release $ARGUMENTS`. It writes nothing to GitHub unless `--post` is in the arguments: add `--post` only when the owner asked
   for it.
2. Show the notes as they are, and say which issues have no `## Result` (their line is the title alone).

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
