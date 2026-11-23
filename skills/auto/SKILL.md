---
name: auto
description: Run a Saani auto run (Claude Code works for hours on the open GitHub issues of the project), interactive in this session or away in a headless session it starts itself. Use when the owner types /saani:auto, /saani:auto here, /saani:auto away, /saani:auto away 12 15 (only those issues), or /saani:auto off.
---

# /saani:auto: a Saani auto run

**Arguments:** `$ARGUMENTS`. A sub-mode first, then issue numbers (with or without `#`, separated by spaces or commas: only those
issues; none: a full run):
- `here`: the **interactive** sub-mode, in this session.
- `away`: the **away** sub-mode, a headless session this skill starts itself.
- neither: ask the owner "here or away?" first (AskUserQuestion, one question, recommending `away` when they are about to leave), never
  guess.
- `off`: the section "off" below.
- `run <id> --folder <path> [--deploy]` (then issue numbers, if any): the inside of an away run, typed by `saani auto away` into the
  headless session, never by the owner: skip "Before the run" but its step 4, then do the run. In this headless session, keep your
  turn open until every background coder is done and the report is written: a turn that ends with coders running lets `claude -p` stop
  them and exit, and nothing lands.

## The two sub-modes

| | **Interactive** (`here`) | **Away** (`away`) |
|---|---|---|
| The owner | at the keyboard | gone after the questions |
| Questions | any time, one at a time, with a recommendation | only before the run; during it none, a question goes in the report |
| Session | this one | a headless `claude -p` session `saani auto away` starts in the background (a minimized window on Windows), with `--permission-mode auto --permission-prompts none` |
| Guard | present mode (`saani auto on --here`: the record without the guard's switch): a prompt is answered by the owner | away mode: `saani auto on` runs in the headless session, so only it is registered; allow or refuse, never ask |
| Stop rules, report, `/saani:merge` | the same | the same |

**Interactive:** "Before the run" steps 0 to 3, then `saani auto on --here --id <id> --folder <run folder>` (alone, like `on`): the
run's record, so the stop rules, the journal and the report work, but not the guard's switch, so this session stays in present mode and
the owner answers the prompts. Then "The run" in this session; ask whenever an answer is needed, one question at a time, with a
recommendation.

**Away:** "Before the run" steps 0 to 3 here, then start the headless session, alone, from the project's main folder:
`saani auto away --id <id> --folder <run folder> [--only <issue numbers>] [--deploy]` (`--deploy` only if the owner said, in their own
words, that the run may deploy). It prints the id, the log (`build/saani-auto-<id>.log`), how to follow the run and how to end it: give
the owner those lines, and stop. This session stays in present mode. If it exits 2 (no `claude` on the PATH, or the session could not
start), give the owner the command it printed, to start by hand from a terminal in the project's main folder. Why headless: an
interactive session can still stop on a prompt the guard never sees (Claude Code falls back to asking after repeated classifier blocks,
a background subagent's prompt waits); `--permission-prompts none` turns every prompt into a refusal Claude reads.

The project's own rules (its CLAUDE.md, its saani.toml) win where they differ from this checklist.

## Before the run (questions first)
0. **The worktrees folder, reachable without prompts.** Run `saani status`. If it prints `warning: worktrees folder ... is not in
   permissions.additionalDirectories`, tell the owner, before anything else, the line it gives to add to the project's committed
   `.claude/settings.json` (a relative path, the same for every developer), and why: otherwise a command of the run that writes in a
   worktree, the coders' included, needs an approval. Never write a settings file yourself: they are the owner's. Go on once they say it is done (an away run is a new session, so
   it reads it; here, it needs a new session) or that they accept the prompts.
1. **Folder.** An auto run works in its own throwaway worktree, never in the owner's folder: run `saani auto` (it makes
   `auto-<YYYY-MM-DD_HHMM>` beside the other worktrees, on the branch `auto/<YYYY-MM-DD_HHMM>` from the integration branch) and say
   its folder. Run every command of the run from that folder (the session cannot change folder: `cd` in each call), and
   `saani start <n>` for each issue from there as usual. `[board] folders` `auto` is not needed. Make it now, while the session is still in
   present mode: once the run is on, the guard refuses plain `saani auto`.
2. **Questions while a question can still be answered.** List the open issues without the in-dev label (`[labels] in_dev` in saani.toml), in the run's order (`saani auto pick`). For
   each one the run would take but cannot do without the owner's answer, ask it now: one question at a time, with a recommendation. With
   issue numbers as arguments, only those.
3. Say the run's id: `YYYY-MM-DD_HHMM` (date and start time), the one `saani auto` printed. Then, away: start it (section "The two
   sub-modes"); here: "The run".
4. **Away only, the headless session's first step: switch it on.** The run's id and folder come from the arguments (`run <id>
   --folder <path>`). Once the questions are answered, run `saani auto on --id <id> --folder <run folder>`
   (`--deploy` only if the owner said, in their own words, that the run may deploy), alone: nothing chained to it, or the guard does not
   register the session. The id and the folder are those of step 1 and 3. On success it prints, as its last line, `saani auto: on <id>
   folder=<path>`, and the guard registers the session from it; from then on the guard decides every tool call (no more questions), and
   `saani auto status` shows the run, its refusals and its limits. If the line is missing, the run is not on: say so and stop (headless,
   `saani auto on` is refused when neither the project's allow rules, `Bash(saani *)`, nor the classifier let it through). The guard
   refuses `saani auto`, `saani auto on` and `saani auto off` inside the run: the run never re-arms or switches itself off.

## The run
0. **Never wait for approval.** A refused call means skip the item, note it for the report, carry on. No force push, no change to the
   project's protected files, no deploy unless the owner started the run for that.
1. **The pipeline, several issues at once.** You are Claude Code, the main session: you brief, decide and land; the coder and the reviewer do the reading
   and the writing (docs/PLUGIN.md, "The pipeline from an issue to dev"). Loop:
   - `saani auto pick --json` says `slots` (how many issues may start now: `[auto] parallel`, at most 2 per project, less those in worktrees) and the `eligible`
     issues in the run's order (milestone by version, bugs first; At a glance fit, blocked, needs the owner, labels, Type and overlapping
     files already checked, `skipped` says why not). With issue numbers as arguments (`/saani:auto 12 15`), run `saani auto pick --json --only 12 15`.
   - For each free slot take the first eligible issue: `saani start <n>` (posts Started), then `saani:coder` in that worktree, **run in the
     background** so several issues progress at once. Model: sonnet; opus for an issue labelled or titled as hard or security. Brief it with
     the issue number, the worktree path and what the issue asks, not with file dumps.
   - **Alone:** a change of a few lines in one or two files (a typo, a sentence, a setting) you make yourself in that worktree: no coder,
     no reviewer, then land it.
   - When a coder is done: `saani:reviewer` on that diff (always for guard, landing, CI and deploy code), and `saani comment <n> review` when the review starts. Real findings go back to
     the same coder (you decide which are real); a new commit.
   - Then `saani land --worktree <path>` (posts On dev). Landings are one at a time, the lock does it. A conflict at landing goes back to
     the coder of that issue.
   - An issue that is blocked or needs the owner: `saani comment <n> blocked` or `question` with the context and a recommendation, and
     move on. A partly blocked issue: the repo's part, comment the rest. Never change an issue's milestone.
   - **As soon as one issue lands, pick again.** `saani auto pick --json` is run again whenever a slot frees, never from a list made at the
     start, so an issue created, relabelled or answered during the run is seen. The loop ends when nothing is running and nothing is eligible.
   - **Before each pick, and whenever a slot frees: `saani auto status --check`** (and after each landing, which frees a slot). Exit 0: go
     on. Exit 3: a stop rule is reached (40 refusals in total, 10 refusals for the same reason in a row, 10 hours, or the run record is
     missing; the output says which). A stopped run ends the normal way: start no new issue; let the running ones finish, or park them
     (`saani comment <n> blocked` with where they stand); then End, step 4 (the full run, the one pull request if anything landed, the
     `saani ci pr` rounds) and step 5 (the report).
   If the change alters what a document describes, the coder updates that document in the same commit (`refs #n`, the trailers).
2. **Cost.** Keep your own context small: briefs and verdicts, not file dumps; the routine roles run on the cheaper models; read a
   result's summary, not its transcript. `saani kpis` at the end gives the cost per issue, role and model.
3. **Review and clean** once the list is exhausted: tests, then code, then docs, in small commits, each landed. The documentation review is
   never skipped.
4. **End** (also after a stop rule). The full run (`[tests] full`); open the ONE integration -> production pull request (milestone set, `refs #n` for every
   in-dev issue); `saani ci pr`. If it is red, read why, fix it (never weaken a test to turn it green), land, run `saani ci pr` again; at
   most `[auto] ci_attempts` attempts (default 3), then leave the pull request open and say what fails. A run never merges, and never runs `saani merge`: that is the owner's word.
5. **Report, then stop.** `saani auto report --pr <N> --post` writes `build/saani-auto-<id>.md` in the main checkout and comments it on the
   pull request; with nothing landed there is no pull request: `saani auto report` alone writes the file, and you say so in the chat.
   It holds the start, end, duration, why the run stopped (a limit, or "finished the list"), the id and the folder's name, the issues landed, the issues left with a question or blocked, the
   refusals by reason, the cost, the timeline and the next step. Add to the chat (headless: your last message, which the owner's
   terminal prints) what only you know: a table of what is pending (issue |
   question with context | options with pros and cons), and which issues waited for overlapping files (best effort: only the paths an issue names between backticks are compared, so an issue that names none never waits). Then stop: the run is over, and the guard still confines this session. Tell the owner to end it from a terminal with `saani auto off --run <id>` (it also lapses after 12 hours). The report ends
   with `/saani:merge <N>` (or `/saani:merge <N> --deploy`) for the owner, saying that it merges the pull request, relabels the issues and removes this run's folder and
   branch by itself: never tell the owner to run `saani ci merged`, a manual `git worktree remove` or `git branch -d`.

## off
`/saani:auto off`: stop now. Say what is left and run `saani auto report` (with `--pr <N> --post` when there is a pull request). Then tell the owner to end the run from a terminal: `saani auto off --run <id>` (the guard refuses it inside the run).

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
