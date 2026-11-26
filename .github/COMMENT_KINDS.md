# Comment kinds

Every comment Claude Code (or a worker) writes on an issue starts with one of these headings, so the owner and the map can scan the
history. Tables and checkboxes over prose. `Started` moves the issue's GitHub status to In progress; `In review`, `On dev` and `On main`
to In review (only forward; docs/SAANI.md, Issue statuses).

| Heading | When | Content |
|---|---|---|
| `### ▶ Started` | `saani start` posts it | branch, worktree folder (`--no-comment` skips it) |
| `### 🔎 In review` | a review begins | who reviews, what is checked; `Reviewed commit` and `Diff hash` (`saani comment <n> review` adds them from the issue's worktree: the next review reads only the commits after that commit, `saani review diff <n>`) |
| `### ⚙ Progress` | a commit or a round | commit, what is done, what is left (checkboxes) |
| `### ❓ Question` | `help wanted` added (`saani comment <n> question` adds it) | context, then a grid: options, pros and cons, recommendation |
| `### ⛔ Blocked` | blocked | by what, what was done meanwhile |
| `### ✅ On dev` | `saani land` posts it | commit, tests run (tier, result), `in-dev` |
| `### 📦 In PR` | `saani ci pr` posts it, once per pull request | pull request, URL |
| `### 🚀 On main` | `saani ci merged` posts it, once per pull request | pull request, merge commit, `in-main` |
| `### ✔ Deployed` | deployed and verified | deploy log, live checks done, follow-up issues |
| `### 🔗 Linked` | a related issue appears | a table: issue, relation |

`saani comment <n> <kind> [--text ...] [--field KEY=VALUE ...]` posts any of them (kinds: started, review, blocked, question, progress,
on-dev, in-pr, on-main, deployed, linked), as a one-row table of the fields, a blank line, then the text, and sets the matching label.

**A decision of the owner** (#102) is a `Progress` comment with a `Decision` column: `saani comment <n> progress --field Decision="<what the owner decided>" [--text "<why, context>"]`. It is the one shape `saani auto pick` reads: the latest `Decision` of an issue is in the coder's brief.

## Examples
```markdown
### ⚙ Progress
| Decision |
|---|
| Keep the cache per worktree, not global |

The owner's answer to the question of the auto run of 2026-11-25.
```

```markdown
### ✅ On dev
| Commit | Tests | Next |
|---|---|---|
| abc1234 | landing tests: 312 passed (3 min) | goes to main with the next pull request |

- [x] criterion 1
- [ ] criterion 2 → follow-up #n
```

```markdown
### ❓ Question
Context: the release gate reads checks only; a gate comment also counts as green.
| Option | For | Against |
|---|---|---|
| Read the gate comment | one source of truth with the release | one more API call |
| Ignore it | simpler | the map shows a green PR as pending |

**Recommendation:** read the gate comment.
```
