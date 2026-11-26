---
name: release
description: Write the release notes from the GitHub issues of a milestone or a pull request (by issue Type, with each issue's Result line and the commits that landed it), open or update the release pull request (dev to main, with Closes and refs lines), or create a GitHub release by hand. Use when the owner types /saani:release notes <milestone>, /saani:release notes --pr <N>, /saani:release pr or /saani:release create <version>.
disable-model-invocation: true
argument-hint: "notes <milestone> | notes --pr <N> [--short] [--post] | pr [--dry-run] | create <version> [--target sha] [--from-pr N | --milestone title | --notes-file file]"
---

# /saani:release: the release notes and the GitHub releases

**Arguments:** `$ARGUMENTS`.

1. Run `saani release $ARGUMENTS`. `notes` writes nothing to GitHub unless `--post` is in the arguments; `create` creates the GitHub
   release `v<version>` (or edits the notes of one that exists: a tag is never moved). Add `--post` only when the owner asked for it.
   `saani merge` already creates the release when a merge changes the version (`[release] version_file`).
   `pr` opens or updates the one integration -> production pull request (`--dry-run` writes nothing) and refuses, with the next step,
   unless the version in `[release] version_file` is higher than production's.
2. Show the output as it is. For notes, say which issues have no `## Result` (their line is the title alone). For `create`, say what it
   did: created, or only edited the notes.

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
