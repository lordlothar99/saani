# Comment kinds

Every comment the Architect (or a worker) writes on an issue starts with one of these headings, so the owner and the map can scan the
history. Tables and checkboxes over prose.

| Heading | When | Content |
|---|---|---|
| `### ▶ Started` | `saani start` posts it | branch, worktree folder (`--no-comment` skips it) |
| `### 🔎 In review` | a review begins | who reviews, what is checked |
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

## Examples
```markdown
### ✅ On dev
| Commit | Tests | Next |
|---|---|---|
| abc1234 | tier 1: 312 passed (3 min) | goes to main with the next pull request |

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
