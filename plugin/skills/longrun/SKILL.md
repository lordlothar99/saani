---
name: longrun
description: Run a Saani long run (the Architect works for hours alone on the open GitHub issues of the project). Use when the owner types /saani:longrun, /saani:longrun 12 15 (only those issues), or /saani:longrun off.
---

# /saani:longrun: a Saani long run

**Arguments:** `$ARGUMENTS`. Empty: a full run. `off`: the section "off" below. Issue numbers (with or without `#`, separated by
spaces or commas): only those issues.

The project's own rules (its CLAUDE.md, its saani.toml) win where they differ from this checklist.

## Before the run (questions first)
1. **Folder.** Say the session's working folder. A long run works in its own checkout (`[board] folders` `longrun` in saani.toml); if the
   session is elsewhere, say so and stop.
2. **Questions while a question can still be answered.** List the open issues without the in-dev label (`[labels] in_dev` in saani.toml), in the run's order (below). For
   each one the run would take but cannot do without the owner's answer, ask it now: one question at a time, with a recommendation. With
   issue numbers as arguments, only those.
3. Say the run's id: `YYYY-MM-DD_HHMM` (date and start time).

## The run
0. **Never wait for approval.** A refused call means skip the item, note it for the report, carry on. No force push, no change to the
   project's protected files, no deploy unless the owner started the run for that.
1. **Which issues: as many as possible.** Milestone order (lowest version first), then issues with no milestone; inside one, bugs first.
   Excluded, real blockers only: the server or a live check, the owner's answer (`[labels] answer`), the owner's action (`[labels] act`), a key or account only the
   owner has. A partly blocked issue: do the repo's part, comment the rest. An unclear one: comment the pending decision (context, options,
   a recommendation) and move on. Never change an issue's milestone. **The next issue is searched again after each one, never taken from a
   list made at the start**, so an issue created, relabelled or answered during the run is seen.
2. **Per issue.** `saani new <n>`; implement; tier 0 while coding, then `saani land` (tier 1 runs there); commit `refs #n` with the
   trailers; comment the commit on the issue; add the in-dev label. If the change alters what a document describes, update that document in the
   same commit.
3. **Review and clean** once the list is exhausted: tests, then code, then docs, in small commits, each landed. The documentation review is
   never skipped.
4. **End.** The full run (`[tests] full`); open the ONE integration -> production pull request (milestone set, `refs #n` for every
   in-dev issue); `saani ci pr`. If it is red, read why, fix it (never weaken a test to turn it green), land, run `saani ci pr` again; at
   most 3 attempts, then leave the pull request open and say what fails. A run never merges unless the owner started it for that.
5. **Report** in the chat and as a comment on the pull request: start, end, duration, id, folder; the issues done; a table of what is
   pending (issue | question with context | options with pros and cons); what was refused; the numbers (`saani kpis`). End with the exact
   commands for the owner, each with its folder and branch.

## off
Say what is left, write the report, stop.
