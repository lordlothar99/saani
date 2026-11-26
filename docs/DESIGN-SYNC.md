# Keeping a project's UI and its Claude Design artifact in step

For a project whose UI lives both in the repo (a file such as a page's HTML) and in a Claude Design artifact. Either side may be newer:
the owner edits in Design, a run fixes things in the repo. The protocol below keeps both, and loses neither side's changes.

**The artifact's link is not in the repo.** A run gets it from the issue (its At a glance row or a comment). No link on the issue: the
run comments the issue as blocked and says so; it never guesses a link.

## The base

The **base** is the version of the page that was last in step on both sides. It is kept in the main checkout's `build/` folder
(git-ignored, so nothing about it is committed), one file per page, for example `build/design-base/<page>.html`. An auto run's guard
only lets it write in the worktrees folder, the run's folder and temp, never the main checkout: a run keeps the base it makes in its
own worktree's `build/` (and says so in its issue comment), and an attended session copies it into the main checkout's `build/`. A
machine without a base starts from "no base" (step 3).

**In an auto run** the guard allows neither `git merge-file` nor `node`: the merge is made by hand with Read, Edit and Write on
copies in the worktree's `build/`, and the syntax check (step 4) is left to the project's tests or an attended session, said in the
comment.

## Sync

1. **Read the artifact's page** (the page the issue names, through the Design connector). Never edit the artifact from a copy that was
   not just read.
2. **Strip the host's wrapper.** The host adds markup around the page (a frame, scripts, styles of its own). Remove exactly what the host
   added, so what is left compares with the repo's file. Keep a note of what was stripped: it goes back when the page is republished.
3. **Three-way merge** the repo's file (ours), the artifact's stripped page (theirs) and the base: `git merge-file <ours> <base> <theirs>`
   (attended sessions; it writes the result into the first file, so run it on copies of the three in `build/`, never on the repo's own
   file first), or by hand when it conflicts or in an auto run.
   - Keep the repo's **review fixes** (a bug, an accessibility or security fix made in the repo and reviewed there).
   - Keep Design's **design choices** (layout, spacing, copy, colour made in Design).
   - A real conflict between the two is not decided by the run: the issue is commented as a question with both versions.
   - No base yet: the first sync is a two-way comparison; show the owner the differences (a question comment) before merging.
4. **Check the result.** `node --check` on the page's script (extract it into `build/` first when it is inline; attended sessions only, see above). A syntax error is fixed
   before anything is saved; the project's own tests run as usual.
5. **Save the new base**: the merged page, in `build/design-base/`. Only after step 4 passes.
6. **Republish when the repo is newer.** If the merged page differs from what the artifact holds, put the wrapper back and republish it
   to the artifact; say in the issue comment that Design was updated. If the artifact was the only newer side, nothing is republished:
   the merged page is committed to the repo.

## Order of work

Check both sides first, sync, then change: a change made on one side before the sync is the one that gets lost. A run that changes the
UI starts with steps 1 to 5, makes its change in the repo, and ends with step 6.
