# Comment kinds

Every comment the Architect (or a worker) writes on an issue starts with one of these headings, so the owner and the map can scan the
history. Tables and checkboxes over prose.

| Heading | When | Content |
|---|---|---|
| `### ▶ Started` | `saani start` | branch, worktree, the plan in three bullets |
| `### ⚙ Progress` | a commit or a round | commit, what is done, what is left (checkboxes) |
| `### ❓ Question` | `help wanted` added | context, then a grid: options, pros and cons, recommendation |
| `### ⛔ Blocked` | blocked | by what, what was done meanwhile |
| `### ✅ On dev` | landed | commit, tests run (tier, result), `in-dev` |
| `### 🚀 On main` | merged into production | pull request, merge commit, `in-main` |
| `### ✔ Deployed` | deployed and verified | deploy log, live checks done, follow-up issues |
| `### 🔗 Linked` | a related issue appears | a table: issue, relation |

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
