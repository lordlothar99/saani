# The map: data contract

The map (`saani/map.html`) reads one JSON object. Served by `saani ui` at `/map`, it reads `/api/state` every 5 s. Published as an
artifact, `/api/state` does not answer and it reads `mock-state.json` beside it (`saani/samples/map-sample.json`, a synthetic sample of an
invented project, also served locally at `/mock-state.json`).

## Two shapes, one page
| Shape | Who gives it | How the page reads it |
|---|---|---|
| `board` (columns) | today's `/api/state` | `normalize()` turns each column into a step: `you`, `ready` -> `backlog` or `todo` by the card's `queue` (`todo` when it has none), or `started` when an agent works on it, `building`, `testing`, `tested` (ready to land: the Loading Yard), `dev`, `main` -> `deploy`, `live` -> `done`; issues the open pull request carries -> `pr` |
| `cards` (flat, with `step`) | the sample; `/api/state` once the lifecycle data model lands | read as is |

**Renamed (#83):** the steps `inbox` and `backlog` are now `backlog` (the Wayfarers' Tents) and `todo` (the Granary), after GitHub's statuses. A sample or a Design `mock-state.json` written before must rename them: the old `backlog` to `todo` first, then the old `inbox` to `backlog`.

## `project`
| Field | Meaning |
|---|---|
| `name`, `repo` | the repository's name and `owner/name` (issue links) |
| `integration`, `production` | the branch names the prompts use |
| `folders` | `work`, `deploy`, `auto`: the folder each prompt says to paste it in |
| `labels` | the label names (`answer`, `act`, `in_dev`, `in_main`, `idea`) |

## `saani`

`{label, kind}`: which Saani serves the map, shown on the last line of the signs. `kind` is `tag` (its code sits on a release tag,
`label` = the tag), `commit` (between releases, `label` = the short commit) or `version` (an installed copy that is not a git checkout,
`label` = `v` + the package version). Absent in the sample: the page says so.

## `cards[]`
| Field | Type | Meaning |
|---|---|---|
| `number`, `title`, `state`, `labels`, `milestone` | as GitHub | the issue |
| `status` | `{name, category}` or `null` | the issue's GitHub status (`In review`, `started`...), read from the REST API with the Type; `null`: none, or the repo has no statuses |
| `type` | `Bug`, `Task`, `Feature`... or `null` | the issue Type GitHub holds (read from the REST API: `gh issue list --json` has none); `null`: none set, the page falls back on the branch prefix and the labels |
| `step` | string | `backlog` (the Wayfarers' Tents: status Backlog or none), `todo` (the Granary: status Todo), `you`, `started`, `building`, `testing`, `landing`, `tested` (the Loading Yard: committed, landing tests green, not landed), `dev`, `pr`, `deploy`, `close`, `checks`, `done` |
| `queue` | `backlog` \| `todo` or absent | `/api/state` only, on a Ready card: where it waits, decided by the server from the GitHub status (`todo` for Todo, In progress and In review; `backlog` for Backlog, none, Done, Not planned, Duplicate), and without statuses from the milestone (a milestone is `todo`); absent (the sample, an older server): `todo` |
| `need` | `answer` \| `act` \| null | waits for the owner: the glowing ring |
| `auto` | `{fit, why, source}` or absent | `fit` is `yes`, `partly` or `no`, `source` is `table`: read from the issue's At a glance table (`saani/glance.py`); absent: the page estimates it from the labels and says so |
| `worktree`, `agents`, `runs`, `prompt` | as `/api/state` | the panel's "Now" grid and the default prompt |
| `detail` | `{summary, criteria[{done, text}], history[{when, kind, text}], url}` or absent | the panel's Context, Acceptance criteria and History: not in `/api/state`, served on demand at `/api/issue?n=<number>` when a panel opens |
| `blocked`, `blocked_by` | bool, issue numbers | the Blocked by row of the At a glance table names an open issue that is not on dev yet, or a date not yet come |
| `anchor` | bool | a tracking issue (an `(epic)`, the live checks): drawn as a tall pillar |
| `staged` | bool | sample only: moved by hand to show a zone |

## The rest
| Field | Meaning |
|---|---|
| `pr` | the open integration -> production pull request: `number`, `url`, `ci.state` (`green`, `red`, `running`, `stale`, `none`: the Watchtower's beacon), `inside` (issue numbers) |
| `roster[]` | every subagent kind: `name`, `kind` (`defined`, `planned`, `built-in`), `model`, `what`, `file`, `runs`, `running[{description, issue?}]` (a plugin's agent, `saani:coder`, is the row of its role, `coder`: its runs count there; the page draws every worker with the look of its role) |
| `milestones[]` | the milestone strip: `title`, `open`, `closed`, `checks` (the number of its open "Live checks ..." issue, or null), `ready` (everything closed, something done) |
| `hygiene[]` | the tidy-up strip, only the kinds that are not empty: `{kind, issues[]}`; `kind` is `closed_labelled` (closed, still labelled in-dev; or in-main when the project has no `[deploy] command` or the issue was closed as not planned / duplicate: with a deploy, a closed in-main issue is To deploy), `no_milestone` (open, not an idea) or `landed_unlabelled` (named by commits on the integration branch, no in-dev / in-main) |
| `crew[]` | the Claude Code sessions: `session`, `name` (folder name only), `branch`, `model`, `state`, `age_min`, `issue` |
| `sample` | sample only: `taken`, `note`, `staged` |
