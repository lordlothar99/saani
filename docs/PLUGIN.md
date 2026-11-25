# The Saani plugin for Claude Code

What a Claude Code session needs to work the Saani way in any project: the auto skill (`/saani:auto`, the main way: a run over the open
issues that ends in one pull request and a report), a slash command for each `saani` command, the worker agents and the guard hook.

| Part | State |
|---|---|
| `skills/<command>/SKILL.md` | `/saani:ui`, `/saani:projects`, `/saani:status`, `/saani:start`, `/saani:land`, `/saani:merge`, `/saani:deploy`, `/saani:comment`, `/saani:test`, `/saani:prune`, `/saani:ci`, `/saani:kpis`, `/saani:release`, `/saani:progress`: each runs that `saani` command and explains the result. `start`, `land`, `merge`, `deploy`, `prune` and `release` (`release notes --post` and `release create` write to GitHub) change the repo, GitHub or production, so Claude never invokes them on its own: the owner types them (`disable-model-invocation`), and `prune` shows a dry run and asks first. Each skill ends with the same "Running saani" section; a test keeps one skill per command and those sections identical |
| `bin/saani`, `bin/saani.cmd` | the `saani` command inside Claude Code: `bin/` is on the PATH of Claude's shell while the plugin is enabled, and the launchers run `saani.py` from the plugin's own copy (bash: `py -3`, else `python`, else `python3`, never the Windows Store stub, and one Python process only, since the guard hook runs through it before every tool call; saani.py refuses a Python older than 3.11; Windows: `python`). `.gitattributes` keeps `bin/saani` in LF, or bash fails on Windows checkouts |
| `skills/auto/SKILL.md` | `/saani:auto here` and `/saani:auto away` (the two sub-modes; none given: it asks), `/saani:auto away 12 15`, `/saani:auto off`, and `/saani:auto run <id> --folder <path>` (the inside of an away run, in the headless session `claude -p ... --permission-mode auto --permission-prompts none` that `saani auto away` starts: [SAANI.md, An auto run](SAANI.md#an-auto-run)): the checklist of an auto run (Claude Code works alone for hours on the open issues, in a throwaway worktree made by `saani auto`; `saani auto` makes the run's worktree and `saani auto on` switches the guard and the run's record on, both before the work, `saani auto status --check` before each pick applies the stop rules, `saani auto report --pr N --post` ends the run's work; the run is ended from outside, since the guard refuses `off` inside it: by the watcher `saani auto away` starts when the session ends, by the skill after a `here` run's report (with `--keep-on`, after the quiet time or a stop rule); else by the owner with `/saani:auto off` or `saani auto off --run <id>` from a terminal) |
| `hooks/hooks.json` | the guard: a PreToolUse hook on every tool and a PostToolUse hook on Bash (`"${CLAUDE_PLUGIN_ROOT}/bin/saani" guard`; on Windows Claude Code runs hooks through Git Bash, so `bin/saani` runs it there too). Present mode (no auto run registered for the session): it prints nothing and Claude Code asks as usual. Once `saani auto on` has run in that session (the owner is asked for it as usual; the PostToolUse hook then registers the session from the line `saani auto: on <id> folder=<path>` the command prints), for the session and its subagents: it allows the run's work at once and refuses the rest with a reason, never asks, and logs each refusal. A 15 s timeout: if the hook cannot run at all, Claude Code's normal permissions apply; a slow decision never gets there, since the guard denies by itself after 9 s (`too slow`). A guardrail, not a sandbox: the tests run the worktree's code. How it decides: [SAANI.md, The guard](SAANI.md#the-guard). The **git** commit-msg hook is not part of the plugin: it is [.githooks/commit-msg](../.githooks/commit-msg), and needs `SAANI_HOME` because git runs it outside Claude Code |
| `agents/<role>.md` | the worker agents, used as `saani:<role>` (Claude Code prefixes every plugin agent with the plugin's name, so a project's own `reviewer` stays distinct): `coder`, `tester`, `reviewer`, `scribe`, `researcher`, `deployer`. Each file names its tools and its model. Agents come only through the plugin, never copied into a project's `.claude/agents/` (a copy would lose the prefix); `agents/` holds only agent files, because every `.md` there is loaded as an agent. `sentinel` waits for its issue |

## Worker roles
Six are written (`agents/`); the models are a first choice, to be changed by a trial on real issues, never by a price list. A role's
model is the `model:` line of its file: Anthropic models only for now (`sonnet`, `haiku`, `opus`), so a cheaper or other-provider model,
or another harness, is a one-line change after the cost per landing is measured (#8). A worker keeps the same limits whatever its model.
The coder, the tester and the reviewer never `cd`: they name the worktree (file tools with its absolute paths, `git -C <worktree>`,
`saani test --worktree <worktree>`) and run one plain command per Bash call, so a project's allow rules cover what they run.

| Role | Model (first guess, to be measured) | Job |
|---|---|---|
| Coder | Sonnet, Opus for hard issues | writes the change for one issue in its own worktree |
| Tester | Haiku | drives `saani test`, reports only failures and what they mean |
| Reviewer | Opus | reviews the diff with fresh eyes, never the coder's reasoning |
| Scribe | Haiku | issue comments, labels, commit trailers |
| Researcher | Sonnet | reads docs, releases and code, brings back the answer |
| Sentinel (planned) | Sonnet | server diagnostics with a read-only key |
| Deployer | Sonnet | runs `saani deploy --dry-run`, reports the risks (the issues and commits in the range), reads a deploy log and says what a failure means; never deploys, never touches keys: the main session, which sees the owner, runs `saani deploy` or `saani merge --deploy` after the owner asks and taps their SSH agent |

## The pipeline from an issue to dev
Claude Code (the main session, strongest model) runs it; the Coder, Tester, Reviewer and Scribe are its hands.

| Step | Who | What |
|---|---|---|
| 1 | Claude Code | `saani start <n>`; reads the issue; decides alone or delegated (below) |
| 2 | `saani:coder` | the change in that worktree, the fast checks, the landing tests through the queue once, just before it hands back, one commit with `refs #n` and the trailers |
| 3 | `saani:reviewer` | the diff and the issue only, never the coder's reasoning; a list of findings. It never runs the whole suite again, only the test files it doubts, and names commits as `HEAD~n` or ranges (the guard refuses raw ids) |
| 4 | `saani:coder` | fixes the findings that are real (Claude Code decides which); a new commit |
| 5 | Claude Code | `saani land --worktree <path>` (the checks, the lock, the landing tests, the push to dev) |
| 6 | `saani:scribe` | a comment on the issue only when `land` has not made it; labels from `saani.toml` |

`saani:tester` drives any run of the tests that Claude Code does not want in its own context (a full run), and `saani:researcher` answers a
question before step 1 or 2. Only Claude Code, the main session, lands, pushes, closes or merges.

**When Claude Code works alone:** a change of a few lines in one or two files, a typo, a document sentence, a setting. A subagent costs a
briefing and a report; below that size it costs more than it saves. **When it delegates:** the change spans many files or needs a long
read; a failure log to read (Tester); a fresh review (Reviewer, always for a change to the guard, the landing or the CI); a question that
needs reading many pages (Researcher). Parallel coders each get their own worktree.

**In an auto run** the pipeline runs for several issues at once (`[auto] parallel`: by default sized from the machine, at most 2; up to 10 when set, with a warning above 5): Claude Code asks
`saani auto pick --json` for the free slots and the next eligible issues, starts each (`saani start`), runs its coder in the background,
and reviews and lands as each coder finishes. Landings are one at a time (the lock); a slot that frees is filled at once by a new
`saani auto pick`. Issues whose likely files overlap are not run side by side. At the end: the full run, one integration -> production
pull request, `saani ci pr`, the report on the pull request; the owner's `/saani:merge` merges it (and creates the GitHub release when the
version changed). See `skills/auto/SKILL.md`.

## The headless session of an away run (#52)
`claude -p` stops its background tasks 10 minutes after the last turn and exits, and the away run has its coders in the background. So
`saani auto away` starts the session with `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` (wait indefinitely) and the skill's away part tells it
to keep its turn open until the coders are done. It also warns when Claude Code does not trust the main folder yet: open Claude Code there
once and accept the dialog. The mechanics (the command by hand, the trust check, the process id) are in docs/SAANI.md, "An auto run".

## Install (checked against the Claude Code docs and `claude plugin validate`, 2026-11-22)
This repository is both the marketplace and the plugin: `.claude-plugin/marketplace.json` lists one plugin whose source is `./`, the repo
root, with `.claude-plugin/plugin.json` beside it. Claude Code copies only a plugin's own folder at install, so the plugin is the whole
repo: that way its copy holds `saani.py` for `bin/saani`. A side effect: a plugin with a `bin/` folder cannot be installed from claude.ai or
Cowork, only in Claude Code (Saani needs git and a local machine anyway).

| Step | In PowerShell | In a Claude Code session |
|---|---|---|
| Add the marketplace (local clone) | `claude plugin marketplace add C:\Users\<you>\projects\saani` | `/plugin marketplace add C:\Users\<you>\projects\saani` (if a drive path is refused: open the clone's folder and use `./`) |
| or from GitHub | `claude plugin marketplace add lordlothar99/saani` | `/plugin marketplace add lordlothar99/saani` |
| Install | `claude plugin install saani@saani` | `/plugin install saani@saani` |
| After a change | `claude plugin marketplace update saani`, then `claude plugin update saani@saani` | the same with `/plugin`, then `/reload-plugins` |

- Skills are namespaced: **`/saani:ui`**, not `/ui`.
- Skills that need a file of the plugin use `${CLAUDE_PLUGIN_ROOT}` (substituted in the skill's text; it is not an environment variable of
  Claude's shell).
- `plugin.json` pins `version`: bump it at each release, or installed copies keep the old one.
- Check before a commit: `claude plugin validate .` (the marketplace and the plugin are the same folder). Two warnings are expected, so
  `--strict` fails: `CLAUDE.md` at the plugin root is not loaded (it is for working on Saani, not shipped as context), and no icon (only a
  plugin-directory listing needs one).
- License: GNU AGPL-3.0-only ([LICENSE](../LICENSE)).
- `saani --version` prints the plugin's version (a test keeps `saani/__init__.py` and `plugin.json` equal), so the command on the PATH and
  `claude plugin list` agree.
- Sources: https://code.claude.com/docs/en/plugins/marketplace-reference.md, .../manifest-reference.md, .../components.md (Executables),
  .../loading.md (In-place and copied plugins), .../install.md, .../cli-reference.md, https://code.claude.com/docs/en/skills (string
  substitutions).
