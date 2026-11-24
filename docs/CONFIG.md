# saani.toml: the settings of a project

One file at the project's root, committed (a worktree reads its own copy). `examples/saani.toml` is a full, commented example. An unknown
setting is refused, so a typo is never silent. Commands are lists; `"{python}"` is replaced by the Python that runs Saani.

| Table | Key | Default | Meaning |
|---|---|---|---|
| `[project]` | `repo` | (required) | GitHub `owner/name` |
| | `owner` | `""` | GitHub login assigned when work on an issue starts (the "in progress" sign); empty: never assign |
| `[branches]` | `integration` | `dev` | where work lands, one landing at a time |
| | `production` | `main` | what is released; the release pull request goes integration -> production |
| | `prod_tag` | `prod` | the tag that marks what is live; a hotfix starts there |
| `[worktrees]` | `folder` | `../<repo name>-wt` | where the workers' worktrees go (relative to the main checkout, or absolute). Outside the main checkout, it must be in Claude Code's `permissions.additionalDirectories`, best in the project's committed `.claude/settings.json` as a relative path (`"../webshop-wt"`, read from the project's folder, so one line covers every developer), or a command that writes there asks the owner: `saani status` warns with the line to add |
| `[issues]` | `types` | `{Task = "task", Feature = "feat", Bug = "bug"}` | GitHub issue Type -> branch prefix; an issue with another Type, or none, is refused |
| | `hotfix_default_type` | `bug` | the prefix of a hotfix whose issue has no Type |
| | `labels` | see below | the labels Saani reads and writes |
| `[status]` | `enabled` | `auto` | GitHub's issue statuses (docs/SAANI.md, Issue statuses): `auto` = used when the repo has them, `off` = labels only |
| | `names` | `{backlog = "Backlog", todo = "Todo", started = "In progress", review = "In review", done = "Done"}` | the status of each step, by name; a step whose name is not found takes the status of its category (review: the last `started` one). Other keys are refused |
| `[commits]` | `trailers` | `["Why", "Undo"]` | the lines each commit on a worker's branch or the integration branch must carry |
| `[tests]` | `landing` | `[]` | before every landing: the tests a change can affect (empty: no tests at landing). The old name `tier1` is still read, with a message to rename it; both at once are refused |
| | `full` | `[]` | everything: before a release, and the PC's CI |
| | `check` | `[]` | quick checks before the full run on the PC |
| | `timing_reports` | `[]` | files copied into a new worktree (the first is the newest timing report among worktrees) |
| `[land]` | `generated` | `[]` | files rebuilt at landing, never by hand; a conflict there takes the integration copy |
| | `rebuild` | `[]` | commands that rebuild them, in order (a list of command lists) |
| | `frozen` | `{}` | `path = "mark"`: once the mark is in the first 2,000 characters of that file on the integration branch, no branch may change it |
| | `lock_stale_hours` | `2` | a landing lock older than this is taken over |
| | `push_attempts` | `3` | how many times a landing merges again when the integration branch moved under it |
| `[deploy]` | `command` | `[]` | the deploy, a command list, e.g. `["ssh", "-i", "{key}", "deploy@host", "deploy-prod", "{sha}"]`; `{key}`, `{sha}` (the commit checked, so the server deploys exactly it) and `{python}` are expanded. Empty: `saani deploy` refuses |
| | `dry_run` | `[]` | the same with a dry-run flag (`{sha}` and `{python}` work, `{key}` too); empty: skipped. Use a gate on the server (docs/SAANI.md, "Deploy"), never a bare forced `command=` |
| | `checks` | `[]` | command lists run before the deploy (e.g. disk space); each must exit 0 (`{sha}`, `{python}` and `{key}` work) |
| | `log_ok` | `""` | a regex the deploy's output must match to count as a success (with exit 0) |
| | `key` | `""` | the path of the deploy key, never its content: only passed through as `{key}` (printed as `<key>`) |
| | `timeout_minutes` | `30` | the deploy is stopped after this long |
| | `paths` | `[]` | path prefixes a served project deploys; an issue on production whose commits touch none of them shows as "To close" on the map (empty: everything on production is "To deploy") |
| `[board]` | `folders` | `{}` | `work`, `deploy`, `auto`: the folders the map's prompts name (read by `saani ui`; the section keeps its old name, `board`). Only these three keys: any other is refused, and the old `longrun` is refused with its new name, `auto` |
| | `session_match` | the repo name | Claude Code sessions whose project folder name contains this are listed |
| | `port` | `8771` | the local port of `saani ui` |
| `[release]` | `version_file` | `""` | the file whose version a merged integration -> production pull request may change (a line like `__version__ = "1.2.0"`, `version = "1.2.0"` or `"version": "1.2.0"`). When the pull request changes it, `saani merge` creates the GitHub release `v<version>` on the merge commit (docs/SAANI.md, Releases). Empty: no release |
| `[auto]` | `parallel` | `0` | issues an auto run keeps in flight: `0` = sized from the machine, min(2, cores // 4), at least 1, and 1 when free RAM cannot hold one issue (4 GB; macOS: cores only); `n >= 1` = fixed; at most 2 per project (another project's run has its own 2) |
| | `ci_attempts` | `3` | an auto run: the most `saani ci pr` rounds (read why it is red, fix, land, run it again) before the pull request is left open |
| `[ci]` | `workflow` | `ci.yml` | the GitHub Actions workflow `saani ci pr` starts (it must accept `workflow_dispatch`) |
| | `free_minutes` | `2000` | free Actions minutes a month for the whole account |
| | `reserve_minutes` | `50` | kept for the end of the month |
| | `pace_slack` | `0.15` | a heavy job goes to the PC when the share of minutes used is ahead of the share of the month passed by more than this |
| | `status_context` | `saani/ci` | the commit status set on the pull request's head |
| | `jobs` | `{pr-ci = {minutes = 20, heavy = true}}` | each job's measured cost (`minutes`); `heavy`; `what`, a note for the reader; `workflow` for a small automatic workflow `ci sync` switches on and off. A job named `relabel` with a `workflow` is the relabel workflow `saani ci merged` checks for |
| | `relabel` | `[]` | a command that swaps `in_dev` for `in_main` on the issues a merged pull request carries, given its number; empty: `saani ci merged` does the swap itself with `gh` |
| `[hints]` | `hotfix_after_merge` | a generic sentence | the text of a hotfix pull request's "after the merge" step (`[hints]` takes free texts: any other key is accepted and not read) |

## Labels
| Key | Default | Meaning |
|---|---|---|
| `in_dev` | `in-dev` | the issue's work is on the integration branch |
| `in_main` | `in-main` | merged into production, not yet deployed and verified |
| `answer` | `help wanted` | waits for the owner's answer |
| `act` | `owner-action` | waits for the owner to do something themselves |
| `idea` | `idea` | a raw idea: off the map, no milestone needed |

## Logs (in `build/` of the main checkout, never committed)
| File | Written by |
|---|---|
| `saani-landings.jsonl` | `saani land` |
| `saani-tests.jsonl` | the test queue |
| `saani-ci.json` | `saani ci` |
| `saani-auto-<id>.jsonl` | an auto run's journal (status comments and landings while the run is on) |
| `saani-auto-<id>.md` | `saani auto report` |
| `saani-auto-<id>.log`, `saani-auto-<id>.pid` | `saani auto away`: the headless session's output and its process id |
| `saani-deploy-<UTC>.log` | `saani deploy` |

The guard's own files (an auto run's registry entries and `refusals.jsonl`) are in `~/.saani/auto/`; the map's project list is
`~/.saani/projects.toml`.
