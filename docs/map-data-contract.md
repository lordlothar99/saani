# The map: data contract

The map (`saani/map.html`) reads one JSON object. Served by `saani ui` at `/map`, it reads `/api/state` every 5 s. Published as an
artifact, `/api/state` does not answer and it reads `mock-state.json` beside it (`saani/samples/map-sample.json`, a synthetic sample of an
invented project, also served locally at `/mock-state.json`).

## Two shapes, one page
| Shape | Who gives it | How the page reads it |
|---|---|---|
| `board` (columns) | today's `/api/state` | `normalize()` turns each column into a step: `you`, `ready` -> `started`, `building`, `testing`, `dev`, `main` -> `deploy`, `live` -> `done`; issues the open pull request carries -> `pr` |
| `cards` (flat, with `step`) | the sample; `/api/state` once the lifecycle data model lands | read as is |

## `project`
| Field | Meaning |
|---|---|
| `name`, `repo` | the repository's name and `owner/name` (issue links) |
| `integration`, `production` | the branch names the prompts use |
| `folders` | `work`, `deploy`, `longrun`: the folder each prompt says to paste it in |
| `labels` | the label names (`answer`, `act`, `in_dev`, `in_main`, `idea`) |

## `cards[]`
| Field | Type | Meaning |
|---|---|---|
| `number`, `title`, `state`, `labels`, `milestone` | as GitHub | the issue |
| `step` | string | `inbox`, `backlog`, `you`, `started`, `building`, `testing`, `landing`, `dev`, `pr`, `deploy`, `close`, `checks`, `done` |
| `need` | `answer` \| `act` \| null | waits for the owner: the glowing ring |
| `longrun` | `{fit, why, source}` or absent | `fit` is `yes`, `partly` or `no`, `source` is `table`: read from the issue's At a glance table (`saani/glance.py`); absent: the page estimates it from the labels and says so |
| `worktree`, `agents`, `runs`, `prompt` | as `/api/state` | the panel's "Now" grid and the default prompt |
| `detail` | `{summary, criteria[{done, text}], history[{when, kind, text}], url}` or absent | the panel's Context, Acceptance criteria and History: not in `/api/state`, served on demand at `/api/issue?n=<number>` when a panel opens |
| `blocked`, `blocked_by` | bool, issue numbers | the Blocked by row of the At a glance table names an open issue that is not on dev yet, or a date not yet come |
| `anchor` | bool | a tracking issue (an `(epic)`, the live checks): drawn as a tall pillar |
| `staged` | bool | sample only: moved by hand to show a zone |

## The rest
| Field | Meaning |
|---|---|
| `pr` | the open integration -> production pull request: `number`, `url`, `ci.state` (`green`, `red`, `running`, `stale`, `none`: the Watchtower's beacon), `inside` (issue numbers) |
| `roster[]` | every subagent kind: `name`, `kind` (`defined`, `planned`, `built-in`), `model`, `what`, `file`, `runs`, `running[{description, issue?}]` |
| `milestones[]` | the milestone strip: `title`, `open`, `closed`, `checks` (the number of its open "Live checks ..." issue, or null), `ready` (everything closed, something done) |
| `hygiene[]` | the tidy-up strip, only the kinds that are not empty: `{kind, issues[]}`; `kind` is `closed_labelled` (closed, still labelled in-dev / in-main), `no_milestone` (open, not an idea) or `landed_unlabelled` (named by commits on the integration branch, no in-dev / in-main) |
| `crew[]` | the Architect sessions: `session`, `name` (folder name only), `branch`, `model`, `state`, `age_min`, `issue` |
| `sample` | sample only: `taken`, `note`, `staged` |
