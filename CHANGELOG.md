# Changelog

## Unreleased

## 0.4.0 (2026-11-23)
- Plugin 0.4.0: auto runs end to end (the guard, stop rules, a two-part launch with the run headless, `saani merge`, `saani deploy`),
  fewer approval prompts (a relative `additionalDirectories` line, agents that never `cd`, `saani test --worktree`), status comments on
  issues, and the map's release pull request as a harvester.
- The main session is called Claude Code, no longer "the Architect", in the docs, the skill, the agents, the map and the kanban page
  (the data keys, such as the role `architect` in the cost figures, are unchanged).
- `saani --version` prints the plugin's version (it said 0.1.0.dev0 under plugin 0.3.0); a test keeps the two equal (issue #40).
- `saani auto on [--id ID] [--folder PATH] [--deploy]`, `saani auto off`, `saani auto status [--check]` and `saani auto report [--pr N] [--post]` (issue #11): the auto run's record in the git folder (the guard registers the session after `saani auto on` ran, cued by the line it prints), the stop rules in one place (`LIMITS`: 40 refusals, the same refusal reason 10 times in a row, 10 hours; `status --check` exits 3 when one is reached), a journal line per status comment and landing (`build/saani-auto-<id>.jsonl`) and the run's report (`build/saani-auto-<id>.md`: landed issues, issues left with a question or blocked, refusals by reason, cost, timeline, next step; `--post` comments it on the pull request). The auto skill switches the run on after its questions, checks the rules before each pick and whenever a slot frees, and ends with the report even after a stop ("Stopped by" row; the report holds the folder's name, never a path); the owner ends the run from a terminal with `saani auto off --run <id>` (the guard refuses `on` and `off` inside a run). `saani auto on` prints one line, `saani auto: on <id> folder=<path>`, the guard's cue to register the session. New setting `[auto] ci_attempts` (default 3): the `saani ci pr` rounds before the pull request is left open.
- The guard: a Claude Code hook shipped by the plugin (`hooks/hooks.json`, `saani guard`, PreToolUse and PostToolUse). Off unless
  `saani auto on` ran in the Claude Code session and its PostToolUse hook registered it (`~/.saani/auto/`); only the owner ends a run
  (`saani auto off --run <id>`). Then it allows the run's work without prompts through a strict allowlist and refuses, with a reason,
  pushes, merges, force flags, curl and wget, secrets, git writes outside the worktrees, edits outside the worktrees and to protected
  files, and counts the refusals by run and project. It fails closed. `bin/saani` starts one Python only (`py -3`, `python`, `python3`,
  never the Windows Store stub).
- `saani auto pick [--json] [--limit N]`: the issues an auto run may take now, in order, from the At a glance tables, with the free slots and why the others are left (blocked, no Type, already in a worktree, overlapping files). `[auto] parallel` sizes the run (`0`: from the cores and free RAM, at most 3). The auto skill drives the pipeline for several issues at once: coder, reviewer, land, the next issue picked as soon as one lands.
- `saani deploy [--dry-run]` (`/saani:deploy`, owner-typed only) and the `saani:deployer` agent (issue #5): CI green on the production head, no issue in production since the prod tag whose At a glance Deploy row says not to deploy, the `[deploy]` checks and dry run, the deploy with a timeout and its log in `build/`, then the prod tag moved (the one force push Saani does: the moving tag) and the `in-main` issues commented `✔ Deployed` and closed. New `[deploy]` settings: `command`, `dry_run`, `checks`, `log_ok`, `key`, `timeout_minutes`; `{key}`, `{sha}` and `{python}` are expanded. A merge commit left by `saani merge` counts as green when its tree equals a green parent's; production is fetched again before the deploy; the output is shown as it comes. The Deploy row marker is `⛔` / `Do not deploy` / `Don't deploy`; "None" and "No" never block. `saani merge --deploy` now runs it.
- `saani comment <n> <kind>`: a status comment in the shape of `.github/COMMENT_KINDS.md` and its label change. `saani start` posts `Started` (`--no-comment` skips it), `saani land` posts `On dev` through it, `saani ci pr` posts `In PR` and `saani ci merged` posts `On main`, each once per pull request and issue.
- `saani merge [PR] [--deploy] [--dry-run]` (`/saani:merge`, owner-typed only): merges the green integration -> production pull request with a merge commit, refusing unless it is mergeable and its CI is green on the exact head, then relabels and removes the finished auto runs (`saani ci merged`). `--deploy` hands over to the deployer (issue #5); until it exists, it exits 2 after the merge.
- The map draws the plugin's agents (`saani:coder`, `saani:reviewer`...) with the look of their role, and counts their runs on the role's row.
- An auto run has two parts: `/saani:auto` asks its questions in the owner's session, then prints the command for the away part, a
  headless session (`claude -p "/saani:auto run <id> --folder <path>" --permission-mode auto --permission-prompts none`) where no
  permission prompt can wait for an answer. `/saani:auto run <id> --folder <path>` is the away part.
- The test tiers are named after their moment (issue #48): **fast** (the coder's quick checks), **landing** (before every landing),
  **full** (before a release). `[tests] tier1` is now `[tests] landing`; the old key is still read, with a message to rename it.
  `saani test --kind landing|full`: `tier1` still runs, as `landing`; `land`, the run inside `saani land`, is no longer offered on the
  command line (it jumped the queue). The guard allows `--kind landing`, `full` and `tier1`.
- `[board] folders` accepts only `work`, `deploy` and `auto` (issue #47): another key is refused, and the old `longrun` is refused
  with its new name, so a renamed folder is never silently ignored.
- Fewer approvals (issue #46): `saani status` warns, with the line to add, when the worktrees folder is not in Claude Code's
  `permissions.additionalDirectories` (a command that writes in a worktree, the agents' included, otherwise asks the owner), and gives
  a relative line (`../webshop-wt`) for the project's committed `.claude/settings.json`, so one line covers every developer; the
  `saani:coder`, `saani:tester` and `saani:reviewer` agents never `cd` (Claude Code asks before every `cd <folder> && <command>`, and a
  subagent's `cd` does not last): they name the worktree with `git -C`, `saani test --worktree` (new option) and `saani land
  --worktree`, run one plain command per Bash call and never inline code; the guard allows `saani test --worktree` only into a worktree's top folder or the run's folder, and checks a test command's paths
  from the top of its checkout (a `cd` into a subfolder no longer shifts the check); the README pairs `Bash(git -C *)` with `ask` rules for
  `git -C ... push`, `reset` and `clean`; the auto skill checks the folder before a run; Saani's own settings carry `../saani-wt` and
  those `ask` rules.
- The map: the release pull request is a harvester at the Refinery (its CI on its lights), a zone's sign opens a panel with one prompt for
  all its issues, a Tweaks menu, and the "Auto mode" filter.

## 0.3.0 (2026-11-22)
- Plugin 0.3.0: the board is the Map (`/map`, `saani map`), the map shows the GitHub issue Type, switching project is a sand storm, long run becomes `auto` and `saani new` becomes `saani start`.
- The `saani` command: `start` (worktree per issue, hotfix from the prod tag), `land` (one landing at a time behind a lock, generated files
  rebuilt, tier 1 through the queue, retries when the integration branch moves), `status`, `prune`, `test` (one test queue for the PC),
  `check-msg` (the commit-msg hook), `ci` (GitHub Actions within the free minutes, the PC for the rest), `kpis`, `ui` (status page and
  isometric map; `map` and `board` are aliases), `progress`.
- Every project setting read from the project's `saani.toml` (docs/CONFIG.md).
- The map (once called the board; `/board` and `saani board` still work) falls back on a synthetic sample, so it works as a published artifact.
- A Claude Code plugin skeleton: the auto skill; this repository is also a plugin marketplace.
- A slash command for every `saani` command (`/saani:ui`, `/saani:start`, `/saani:land`...). The whole repository is the plugin, and its
  `bin/saani` puts the command on the PATH of Claude Code's shell, so nothing else needs setting up.
- The worker subagents `saani:coder`, `saani:tester`, `saani:reviewer`, `saani:scribe` and `saani:researcher` (`agents/`), and the pipeline from an issue to dev.
- A workflow that deletes the head branch of a pull request merged into the production branch, except `dev`, `main` and `master`.
- `saani ci merged <PR>` relabels `in-dev` to `in-main` itself with `gh` when no workflow or `[ci] relabel` command does, and, like `saani prune`, removes the finished auto runs (worktree and branch).
- Two renames: the long run is now **auto** (`saani auto`, `/saani:auto`, worktree `auto-<id>`, branch `auto/<id>`, the At a glance row `Auto`, `[board] folders` key `auto`), and `saani new` is now `saani start` (`/saani:start`). No aliases; old issues with a `Long run` row and old `longrun/*` branches are still read and pruned.
