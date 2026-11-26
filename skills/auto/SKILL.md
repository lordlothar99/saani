---
name: auto
description: Run a Saani auto run (Claude Code works for hours on the open GitHub issues of the project), interactive in this session or away in a headless session it starts itself. Use when the owner types /saani:auto, /saani:auto here, /saani:auto away, /saani:auto away 12 15 (only those issues), /saani:auto away --keep-on (the run stays on and takes new eligible issues), or /saani:auto off.
---

# /saani:auto: a Saani auto run

**Arguments:** `$ARGUMENTS`. A sub-mode first, then `--keep-on` if the owner typed it (`--wait` is not an option here: it is refused), then issue numbers (with or without `#`,
separated by spaces or commas: only those issues; none: a full run). The run is switched off by itself when it is done; `--keep-on` is the
**keep-on mode** (section "Keep-on mode"): the run stays on and takes new eligible issues when its list is empty (`/saani:auto away --keep-on`,
`/saani:auto here --keep-on`).
- `here`: the **interactive** sub-mode, in this session.
- `away`: the **away** sub-mode, a headless session this skill starts itself.
- neither: ask the owner "here or away?" first (AskUserQuestion, one question, recommending `away` when they are about to leave), never
  guess.
- `off`: the section "off" below.
- `run <id> --folder <path> [--deploy] [--keep-on]` (then issue numbers, if any): the inside of an away run, typed by `saani auto away` into the
  headless session, never by the owner: skip "Before the run" but its step 4, then do the run. In this headless session, keep your
  turn open until every background coder is done and the report is written: a turn that ends with coders running lets `claude -p` stop
  them and exit, and nothing lands. **Never end a turn with a text-only message while an issue is in flight** ("I am waiting for its
  hand-back" ends the session): wait with `saani auto wait` (section "The run", step 0).

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
`saani auto away --id <id> --folder <run folder> [--only <issue numbers>] [--deploy] [--keep-on]` (`--deploy` only if the owner said, in
their own words, that the run may deploy; `--keep-on` only if the owner typed it). It starts a small watcher, detached, which runs the
headless session and, when the session ends (done, stopped or failed), switches the run off by itself and logs it.
It prints the id, the log (`build/saani-auto-<id>.log`), how to follow the run and how it ends: give the owner those lines, and stop.
This session stays in present mode. If it exits 2 (no `claude` on the PATH, or the session could not start), give the owner the command
it printed, to start by hand from a terminal in the project's main folder, and say that a run started by hand has no watcher: they end
it with `/saani:auto off` when the session is over. Why headless: an interactive session can still stop on a prompt the guard never sees
(Claude Code falls back to asking after repeated classifier blocks, a background subagent's prompt waits); `--permission-prompts none`
turns every prompt into a refusal Claude reads. `saani auto away` refuses while a run is on: say so and ask the owner to end it first.

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
   issue numbers as arguments, only those. If the answer lists `owner_running` (the output says "the owner's worktrees #n are in
   flight"), tell the owner before they go away: those worktrees take no slot of the run, but the files they change are not given to
   another issue, and an issue that needs a `gh` form the guard refuses (for example `gh release create`) will be left for them.
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
   project's protected files, no deploy unless the owner started the run for that. **Never write a wait loop** (`until`, `while`,
   `sleep`): the guard refuses them. Wait only with `saani auto wait --minutes 8` (at most 9.5; a coder is running and there is nothing else to do:
   **alone, one Bash call with the timeout set to 600000 ms**; it blocks with no model and returns when a coder of the run hands back, at a
   stop rule, or after the minutes, with JSON `reason`, `finished`, `in_flight`; then read the coders' results, review, land, pick again, and
   call it again while anything is in flight), `saani auto pick --wait <minutes>` (nothing running, nothing eligible), `saani land
   --wait` (the landing lock) or `saani auto status --check`. Never end a turn with a text-only message while an issue is in flight. If
   `auto wait` fails, call it again; run git as `git -C <worktree> <command>` and `saani` with `--worktree`, never
   `cd` to a folder outside the worktrees.
1. **The pipeline, several issues at once.** You are Claude Code, the main session: you brief, decide and land; the coder and the reviewer do the reading
   and the writing (docs/PLUGIN.md, "The pipeline from an issue to dev"). Loop:
   - `saani auto pick --json` says `slots` (how many issues may start now: `[auto] parallel`: default sized from the machine, at most 2; a fixed number up to 10, a `warning` above 5; less the issues this run started: the owner's own worktrees, listed in `owner_running`,
     take no slot, and neither does an issue you parked with `saani comment <n> blocked`, listed in `parked`: its worktree stays and `pick` never offers it again, also with `--only <n>`; only `saani start <n>` or its landing re-admits it; a `question` comment does not park an issue; `note` says "no slot" or "nothing eligible") and the `eligible`
     issues in the run's order (milestone by version, bugs first; At a glance fit, blocked, needs the owner, labels, Type and overlapping
     files already checked, `skipped` says why not). With issue numbers as arguments (`/saani:auto away 12 15`), run `saani auto pick --json --only 12 15`.
   - For each free slot take the first eligible issue: `saani start <n>` (posts Started), then `saani:coder` in that worktree, **run in the
     background** so several issues progress at once. Model: sonnet; opus for an issue labelled or titled as hard or security. Brief it with
     the issue number, the worktree path and what the issue asks, not with file dumps. Also in the brief, the command forms (a refusal
     counts toward the 40 and a refused `git commit` leaves the issue uncommitted): read with Read, search with Grep (no `sed`, `cat`,
     `git grep`); git as `git -C <worktree> ...`, one plain command per call, not `cd`; no `$`, `$(...)`, backticks or redirections
     (only `2>&1`, `>/dev/null`, `2>/dev/null`; a quoted here-document is allowed for a commit message only, and a headless session's
     own permissions refused it once); the commit message written to a file in the worktree's `build/` folder, then `git -C <worktree> commit -F <file>`.
     Anything the owner decided that the coder needs (an answer, a rule) is on the issue, never only in your memory: a headless
     session sees the repo and the issues and nothing else. A design artifact link comes from the issue (docs/DESIGN-SYNC.md).
   - **Alone:** a change of a few lines in one or two files (a typo, a sentence, a setting) you make yourself in that worktree: no coder,
     no reviewer, then land it.
   - When a coder is done: `saani:reviewer` on that diff (always for guard, landing, CI and deploy code), and `saani comment <n> review` when the review starts. Real findings go back to
     the same coder (you decide which are real); a new commit.
   - Then `saani land --worktree <path>` (posts On dev). Landings are one at a time, the lock does it. A conflict at landing goes back to
     the coder of that issue.
   - An issue that is blocked or needs the owner: `saani comment <n> blocked` or `question` with the context and a recommendation, and
     move on. A partly blocked issue: the repo's part, comment the rest. Never change an issue's milestone.
   - **As soon as one issue lands, pick again.** `saani auto pick --json` is run again whenever a slot frees, never from a list made at the
     start, so an issue created, relabelled or answered during the run is seen. The loop ends when nothing is running and nothing is eligible
     (with `--keep-on`, it waits instead: see "Keep-on mode").
   - **Before each pick, and whenever a slot frees: `saani auto status --check`** (and after each landing, which frees a slot). Exit 0: go
     on. Exit 3: a stop rule is reached (40 refusals in total, 10 refusals for the same reason in a row, 10 hours, or the run record is
     missing; the output says which). A stopped run ends the normal way: start no new issue; let the running ones finish, or park them
     (`saani comment <n> blocked` with where they stand); then End, step 4 (the full run, the one pull request if anything landed, the
     `saani ci pr` rounds, when the branch is ahead) and step 5 (the report).
   If the change alters what a document describes, the coder updates that document in the same commit (`refs #n`, the trailers).
2. **Cost.** Keep your own context small: briefs and verdicts, not file dumps; the routine roles run on the cheaper models; read a
   result's summary, not its transcript. `saani kpis` at the end gives the cost per issue, role and model.
3. **Review and clean** once the list is exhausted: tests, then code, then docs, in small commits, each landed. The documentation review is
   never skipped.
4. **End** (also after a stop rule, and also when the run landed nothing). Run `saani auto end --json`: it says whether the integration branch is ahead of
   production (`ahead`), whether a release pull request is open (`pr`) and what to do (`action`: `open`, `update` or `none`). `none`: no pull request; the report says so.
   Otherwise the full run (`[tests] full`); open the ONE integration -> production pull request (milestone set, `refs #n` for every
   in-dev issue) or, with `update`, update the one already open (its description too). Write the description to a file and pass it with `--body-file` to both `gh pr create` and `gh pr edit` (the guard refuses a here-document or an inline body on `gh pr edit`). `saani auto end` fetches origin first and its JSON `landed_count` is the number of issues the run landed. Then `saani ci pr`. If it is red, read why, fix it (never weaken a test to turn it green), land, run `saani ci pr` again; at
   most `[auto] ci_attempts` attempts (default 3), then leave the pull request open and say what fails. When `note` is not null (the pull request does not change the version in `[release] version_file`,
   or none is set), put it in the pull request's description and the chat: "no version bump: merging creates no release; bump it or merge as is". The run never bumps the version itself: the owner decides at merge time
   whether it is a release. A run never merges, and never runs `saani merge`: that is the owner's word.
5. **Report, then stop.** Always, also when the run did nothing (no slot, nothing eligible, a stop rule): the reason comes from the
   journal (`saani auto pick` writes why it gave nothing, `status --check` the stop rule), and `saani auto report` prints the path of the
   file it writes. (If the session ends without one, the watcher writes it.) `saani auto report --pr <N> --post` writes `build/saani-auto-<id>.md` in the main checkout and comments it on the
   pull request; when the branch was not ahead there is no pull request: `saani auto report` alone writes the file, and you say so in the chat. The report's "Release pull request" section says why there is one even though the run landed nothing ("the integration branch was ahead: N commits").
   It holds the start, end, duration, why the run stopped (a limit, or "finished the list"), the id and the folder's name, the issues landed, the issues left with a question or blocked, the
   refusals by reason, the cost, the timeline and the next step. Add to the chat (headless: your last message, which the owner's
   terminal prints) what only you know: a table of what is pending (issue |
   question with context | options with pros and cons), and which issues waited for overlapping files (best effort: only the paths an issue names between backticks are compared, so an issue that names none never waits). Then stop: the run is over. A `here` run: end it yourself with `saani auto off --run <id>` after the report (this session is not guarded). An away run's headless session: the guard confines it and refuses `saani auto off`, and the watcher switches the run off when this session ends. In keep-on mode, if an issue landed after the pull request was opened (a fix of the `saani ci pr` rounds, or an issue taken during the wait), it joined that pull request (it runs from the integration branch): say so in the chat and in the report. The report ends
   with `/saani:merge <N>` (or `/saani:merge <N> --deploy`) for the owner, saying that it merges the pull request, relabels the issues and removes this run's folder and
   branch by itself: never tell the owner to run `saani ci merged`, a manual `git worktree remove` or `git branch -d`.

## Keep-on mode (`--keep-on`)
The guard allows `saani auto pick --wait`; if a call is refused anyway, say so in the report.
Only when the owner typed `--keep-on` (here or away; headless: the `run` arguments carry it). It changes one thing: what the loop does when
nothing is running and nothing is eligible. Without `--keep-on` the run goes to "Review and clean" and the End. With it:
1. Run `saani auto status --check` (exit 3: a stop rule, go to the End). Then run `saani auto pick --wait 8 --json` (add `--only ...`
   as before; the pick takes every ready issue except those in Backlog, whatever their other GitHub status or none), **alone, as one Bash call with the timeout set to 600000 ms** (a Bash call lasts 10 minutes at most; 8 leaves room for
   the reads of GitHub and git). It blocks, with no model and no tokens, and returns when an issue is eligible and a slot is free,
   after 8 minutes with an empty `eligible`, or at once with a `stop` list (a stop rule is reached, or the run record is missing).
   Call it only when nothing is running and nothing is eligible, never while a coder works. If the call fails or times out, treat it as
   an empty wait of 8 minutes (add 8 to the quiet time), run `saani auto status --check`, and carry on; when it fails twice in a row,
   the run ends the normal way (the End).
2. `eligible` not empty: back to the loop (take the issues as usual). `stop` present: the End. Empty: add its `waited_minutes` to the
   quiet time you keep (reset to 0 whenever anything ran); at `keep_on_quiet_minutes` (in the answer, `[auto] keep_on_quiet_minutes`, default 60)
   of quiet the run is done: do "Review and clean" and the End the normal way (the full run, the one pull request, `saani ci pr`, the
   report). Otherwise call step 1 again.
The wait is a Bash call that returns, so the headless session keeps its turn open and `claude -p` does not exit. The stop rules (10
hours, refusals) apply as always. The pull request is one per run; an issue that lands after it is opened joins it.

## off
`/saani:auto off`: stop now (also the end of a run started by hand, or one kept on in keep-on mode). Say what is left and run `saani auto report` (with `--pr <N> --post` when there is a pull request; skip it if the report is already posted). Then end the run: `saani auto status` gives its id; in a session the guard does not confine (the owner's own session: a `here` run, or the session that started an away run), run `saani auto off --run <id>` yourself and show its last line. Only inside the run's guarded session (the headless session of an away run), where the guard refuses it so a run never switches itself off, tell the owner to send `/saani:auto off` in their own session, or to run `python -I <project>/saani.py auto off --run <id>` from a terminal in the project's folder (`saani` alone is often not on a plain terminal's PATH).

## Running saani
Run `saani` with the Bash tool: the plugin's `bin/` puts it on that shell's PATH. If it is not found, run
`python -I "${CLAUDE_PLUGIN_ROOT}/saani.py"` instead. Run it from the project's folder (where saani.toml is). A command that fails: show its
message as it is, say what it means, never work around it.
