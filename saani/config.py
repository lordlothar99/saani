"""Saani's settings for one project: read from `saani.toml` at the project's root (docs/CONFIG.md). Standard library only.

Every module asks `current()` for the settings at call time, so a test can put its own with `use(Config(...))`. The project is the git
checkout of the current folder; its main checkout (the first worktree) holds the logs (`build/`), the worktrees sit beside it.
"""
import dataclasses
import os
import pathlib
import re
import subprocess
import sys
import tomllib

FILE = "saani.toml"


@dataclasses.dataclass
class Config:
    repo: str = "owner/name"                     # GitHub owner/name
    owner: str = ""                              # GitHub login of the person Saani works for: assigned when work starts
    integration: str = "dev"                     # where work lands, one landing at a time
    production: str = "main"                     # what is released
    prod_tag: str = "prod"                       # the tag that marks what is live (a hotfix starts there)
    worktree_folder: str = ""                    # where the workers' worktrees go; default ../<name>-wt beside the main checkout
    types: dict = dataclasses.field(default_factory=lambda: {"Task": "task", "Feature": "feat", "Bug": "bug"})
    hotfix_default_type: str = "bug"             # an urgent fix is never refused for a missing issue Type
    trailers: tuple = ("Why", "Undo")            # each commit on a worker's branch or the integration branch carries them
    landing: list = dataclasses.field(default_factory=list)     # the tests a change can affect, before every landing
    full: list = dataclasses.field(default_factory=list)        # everything, before a release
    check: list = dataclasses.field(default_factory=list)       # quick repository checks run before the full run on the PC
    generated: tuple = ()                        # files rebuilt by `land`, never by hand (a conflict there takes the integration copy)
    rebuild: list = dataclasses.field(default_factory=list)     # commands that rebuild them, in order
    frozen: dict = dataclasses.field(default_factory=dict)      # path -> mark: once the mark is on the integration branch, no change
    timing_reports: tuple = ()                   # copied into a new worktree so its first test estimate is not blind
    lock_stale_hours: float = 2
    push_attempts: int = 3
    labels: dict = dataclasses.field(default_factory=lambda: {"in_dev": "in-dev", "in_main": "in-main", "answer": "help wanted",
                                                              "act": "owner-action", "idea": "idea"})
    folders: dict = dataclasses.field(default_factory=dict)     # work / deploy / auto: the folders named in the map's prompts
    session_match: str = ""                      # the map lists Claude Code sessions whose project folder name contains this
    deploy_paths: tuple = ()                     # path prefixes a served project deploys; an issue on production touching none of them is "to close"
    deploy_command: list = dataclasses.field(default_factory=list)    # the deploy (`saani deploy`); {key} and {python} expanded
    deploy_dry_run: list = dataclasses.field(default_factory=list)    # the same with a dry-run flag; empty: skipped
    deploy_checks: list = dataclasses.field(default_factory=list)     # command lists run before the deploy; each must exit 0
    deploy_log_ok: str = ""                      # a regex the deploy's output must match to count as a success
    deploy_key: str = ""                         # the PATH of the deploy key, never its content: only passed through
    deploy_timeout_minutes: float = 30
    board_port: int = 8771
    parallel: int = 0                            # issues an auto run keeps in flight: 0 = sized from the cores and free memory, capped at 3
    ci_attempts: int = 3                         # an auto run: the most `saani ci pr` rounds (fix, land, again) before it leaves the pull request open
    ci_workflow: str = "ci.yml"
    ci_free_minutes: int = 2000
    ci_reserve_minutes: int = 50
    ci_pace_slack: float = 0.15
    ci_status_context: str = "saani/ci"
    ci_jobs: dict = dataclasses.field(default_factory=lambda: {
        "pr-ci": {"minutes": 20, "heavy": True, "what": "the CI of the integration -> production pull request"}})
    relabel: list = dataclasses.field(default_factory=list)     # command that swaps in-dev for in-main after a merge, run on the PC
    hints: dict = dataclasses.field(default_factory=dict)       # free texts: hotfix_after_merge, deploy
    root: pathlib.Path = dataclasses.field(default_factory=pathlib.Path.cwd)   # the project's main checkout

    @property
    def name(self):
        return self.repo.split("/")[-1]

    @property
    def wt_dir(self):
        """The folder of the workers' worktrees."""
        if self.worktree_folder:
            p = pathlib.Path(self.worktree_folder)
            return p if p.is_absolute() else (self.root / p).resolve()
        return self.root.parent / f"{self.name}-wt"

    @property
    def worker_branch(self):
        """`task/12-some-words`: a worker's branch, its prefix one of the issue types'."""
        return re.compile(r"^(" + "|".join(map(re.escape, sorted(set(self.types.values())))) + r")/(\d+)-[a-z0-9-]+$")

    @property
    def hotfix_branch(self):
        return re.compile(r"^(" + "|".join(map(re.escape, sorted(set(self.types.values())))) + r")/(\d+)-hotfix(-[a-z0-9-]+)?$")

    def command(self, name):
        """A command list from the settings, `{python}` replaced by this interpreter."""
        return [sys.executable if part == "{python}" else part for part in getattr(self, name)]


FOLDER_KEYS = ("work", "deploy", "auto")                     # [board] folders: the folders the map's prompts name
RENAMED_FOLDERS = {"longrun": "auto"}                       # old keys, refused with the new name
RENAMED_TESTS = {"tier1": "landing"}                        # old [tests] keys, still read, with a message naming the new one
_warned = set()


def warn(message):
    """A message on stderr, once per process (a setting still read under its old name)."""
    if message not in _warned:
        _warned.add(message)
        print(message, file=sys.stderr)


def from_toml(data, root):
    """A Config from the parsed TOML (docs/CONFIG.md for the tables). Unknown keys are refused, so a typo is never silent."""
    flat = {}
    maps = {("project", "repo"): "repo", ("project", "owner"): "owner",
            ("branches", "integration"): "integration", ("branches", "production"): "production", ("branches", "prod_tag"): "prod_tag",
            ("worktrees", "folder"): "worktree_folder",
            ("issues", "types"): "types", ("issues", "hotfix_default_type"): "hotfix_default_type", ("issues", "labels"): "labels",
            ("commits", "trailers"): "trailers",
            ("tests", "landing"): "landing", ("tests", "full"): "full", ("tests", "check"): "check",
            ("tests", "timing_reports"): "timing_reports",
            ("land", "generated"): "generated", ("land", "rebuild"): "rebuild", ("land", "frozen"): "frozen",
            ("land", "lock_stale_hours"): "lock_stale_hours", ("land", "push_attempts"): "push_attempts",
            ("deploy", "paths"): "deploy_paths", ("deploy", "command"): "deploy_command", ("deploy", "dry_run"): "deploy_dry_run",
            ("deploy", "checks"): "deploy_checks", ("deploy", "log_ok"): "deploy_log_ok", ("deploy", "key"): "deploy_key",
            ("deploy", "timeout_minutes"): "deploy_timeout_minutes", ("board", "folders"): "folders", ("board", "session_match"): "session_match", ("board", "port"): "board_port",
            ("ci", "workflow"): "ci_workflow", ("ci", "free_minutes"): "ci_free_minutes", ("ci", "reserve_minutes"): "ci_reserve_minutes",
            ("ci", "pace_slack"): "ci_pace_slack", ("ci", "status_context"): "ci_status_context", ("ci", "jobs"): "ci_jobs",
            ("auto", "parallel"): "parallel", ("auto", "ci_attempts"): "ci_attempts", ("ci", "relabel"): "relabel", ("hints",): "hints"}
    for table, values in data.items():
        if table == "hints":
            flat["hints"] = dict(values)
            continue
        if not isinstance(values, dict):
            raise SystemExit(f"{FILE}: `{table}` must be a table")
        for key, value in values.items():
            if table == "tests" and key in RENAMED_TESTS:
                new = RENAMED_TESTS[key]
                if new in values:
                    raise SystemExit(f"{FILE}: [tests] {key} is now [tests] {new}: keep only {new}")
                warn(f"{FILE}: [tests] {key} is now [tests] {new} (rename the key; read as {new} meanwhile)")
                key = new
            field = maps.get((table, key))
            if not field:
                raise SystemExit(f"{FILE}: unknown setting [{table}] {key} (docs/CONFIG.md lists them)")
            flat[field] = value
    folders = flat.get("folders", {})
    if not isinstance(folders, dict):
        raise SystemExit(f"{FILE}: [board] folders must be a table, e.g. {{ work = \"...\", deploy = \"...\", auto = \"...\" }}")
    for key, value in folders.items():
        if key in RENAMED_FOLDERS:
            raise SystemExit(f"{FILE}: [board] folders: `{key}` is now `{RENAMED_FOLDERS[key]}` (rename the key)")
        if key not in FOLDER_KEYS:
            raise SystemExit(f"{FILE}: unknown [board] folders key `{key}` (allowed: {', '.join(FOLDER_KEYS)}; docs/CONFIG.md)")
        if not isinstance(value, str):
            raise SystemExit(f"{FILE}: [board] folders {key} must be a path (a string)")
    for key in ("trailers", "generated", "timing_reports", "deploy_paths"):
        if key in flat:
            flat[key] = tuple(flat[key])
    if "labels" in flat:
        flat["labels"] = {**Config().labels, **flat["labels"]}
    par = flat.get("parallel", 0)
    if not isinstance(par, int) or isinstance(par, bool) or par < 0:
        raise SystemExit(f"{FILE}: [auto] parallel must be a whole number, 0 or more")
    att = flat.get("ci_attempts", 3)
    if not isinstance(att, int) or isinstance(att, bool) or att < 1:
        raise SystemExit(f"{FILE}: [auto] ci_attempts must be a whole number, 1 or more")
    cfg = Config(**flat, root=pathlib.Path(root))
    if "/" not in cfg.repo:
        raise SystemExit(f"{FILE}: [project] repo must be owner/name")
    return cfg


def main_checkout(start=None):
    """The main checkout of the git repository holding `start` (the first worktree), or None outside git."""
    r = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=start or os.getcwd(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        if line.startswith("worktree "):
            return pathlib.Path(line[9:])
    return None


def load(start=None):
    """The settings of the project holding `start`: its checkout's saani.toml (a worktree has it too), else the main checkout's."""
    here = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start or os.getcwd(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    main = main_checkout(start)
    if here.returncode != 0 or main is None:
        raise SystemExit("saani runs inside a git checkout of the project (no git repository here)")
    for folder in (pathlib.Path(here.stdout.strip()), main):
        path = folder / FILE
        if path.is_file():
            return from_toml(tomllib.loads(path.read_text(encoding="utf-8")), main)
    raise SystemExit(f"no {FILE} at the project's root ({main}): copy examples/saani.toml from Saani and fill it (docs/CONFIG.md)")


_current = None


def current():
    global _current
    if _current is None:
        _current = load()
    return _current


def use(cfg):
    """Put these settings in place (tests, or a caller that loaded them itself)."""
    global _current
    _current = cfg
    return cfg
