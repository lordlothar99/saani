"""The repository itself: the plugin and its marketplace agree, the samples stay invented, no one's own path ships, the command starts."""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return [ROOT / p for p in out.splitlines() if (ROOT / p).is_file()]


def test_the_plugin_and_its_marketplace_agree():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
    assert (ROOT / entry["source"]).resolve() == ROOT  # the whole repo is the plugin, so its copy holds saani.py for bin/saani
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"])


def test_every_skill_has_a_name_and_a_description():
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        head = skill.read_text(encoding="utf-8").split("---")[1]
        assert re.search(r"^name: \S+", head, re.M), skill
        assert re.search(r"^description: .{20,}", head, re.M), skill


def test_every_command_has_a_skill_that_runs_it_the_same_way():
    """One /saani:<command> skill per subcommand of saani (check-msg is the git hook, guard the Claude Code hook), each with the same
    "Running saani" section."""
    out = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "--help"], capture_output=True, text=True, encoding="utf-8").stdout
    aliases = {a.strip() for found in re.findall(r"^\s+[a-z-]+ \(([a-z, -]+)\)", out, re.M) for a in found.split(",")}
    commands = set(re.search(r"\{([a-z,-]+)\}", out).group(1).split(",")) - {"check-msg", "guard"} - aliases
    skills = ROOT / "skills"
    assert commands <= {p.name for p in skills.iterdir() if (p / "SKILL.md").is_file()}
    sections = {c: (skills / c / "SKILL.md").read_text(encoding="utf-8").split("## Running saani", 1) for c in commands}
    assert all(len(s) == 2 for s in sections.values()), [c for c, s in sections.items() if len(s) != 2]
    assert len({s[1] for s in sections.values()}) == 1, "the Running saani sections differ"


def test_the_launchers_run_saani_py_from_the_plugin():
    """bin/ is on the PATH of Claude Code's shell while the plugin is enabled: bin/saani (bash) and bin/saani.cmd (Windows) run saani.py."""
    script = (ROOT / "bin" / "saani").read_bytes()
    assert script.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r" not in script  # bash fails on CRLF; .gitattributes keeps it LF
    mode = subprocess.run(["git", "ls-files", "-s", "bin/saani"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert mode.startswith("100755"), "bin/saani must be executable in git"
    assert "saani.py" in (ROOT / "bin" / "saani.cmd").read_text(encoding="utf-8")


def test_ui_and_its_aliases_map_and_board_print_the_same_state(monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("saani_cli", ROOT / "saani.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    from saani import board_server
    monkeypatch.setattr(board_server, "sample_resources", lambda: None)
    monkeypatch.setattr(board_server, "state", lambda: {"time": "now", "project": {"repo": "acme/webshop"}})
    printed = []
    for name in ("ui", "map", "board"):
        assert cli.main([name, "--json"]) == 0
        printed.append(json.loads(capsys.readouterr().out))
    assert printed[0] == printed[1] == printed[2] == {"time": "now", "project": {"repo": "acme/webshop"}}


def test_no_personal_path_ships():
    """Paths in docs and samples use <you> or an invented project, never a real home folder."""
    home = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<you>)[^\\/\s`\"']+|/home/(?!<you>)\w+|/Users/(?!<you>)\w+")
    found = []
    for path in tracked_files():
        if path.suffix in (".png", ".jpg", ".ico"):
            continue
        for n, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if home.search(text):
                found.append(f"{path.relative_to(ROOT)}:{n}: {text.strip()[:100]}")
    assert not found, "\n".join(found)


def test_the_map_sample_is_an_invented_project():
    sample = json.loads((ROOT / "saani" / "samples" / "map-sample.json").read_text(encoding="utf-8"))
    text = json.dumps(sample)
    assert "acme" in text or "webshop" in text


def test_the_command_starts_and_says_its_version():
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "--version"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert re.search(r"\d+\.\d+", r.stdout + r.stderr)


def test_the_license_is_the_same_everywhere():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
    assert "Version 3, 19 November 2007" in license_text
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["license"] == "AGPL-3.0-only"
    assert "AGPL-3.0-only" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_every_worker_agent_is_named_like_its_file_and_has_tools_and_a_model():
    """agents/ holds only agent files; each is named like its file (used as saani:<role>), with a description, tools and a model."""
    folder = ROOT / "agents"
    files = sorted(folder.iterdir())
    assert {p.stem for p in files} >= {"coder", "tester", "reviewer", "scribe", "researcher"}
    for agent in files:
        assert agent.suffix == ".md", agent
        text = agent.read_text(encoding="utf-8")
        head = text.split("---")[1]
        assert re.search(rf"^name: {agent.stem}$", head, re.M), agent
        assert re.search(r"^description: .{20,}", head, re.M), agent
        assert re.search(r"^tools: \S+", head, re.M), agent
        assert re.search(r"^model: (sonnet|opus|haiku)$", head, re.M), agent
    reviewer = (folder / "reviewer.md").read_text(encoding="utf-8").split("---")[1]
    assert not re.search(r"\b(Edit|Write)\b", re.search(r"^tools: .*$", reviewer, re.M).group(0)), "the reviewer is read-only"


def test_the_agents_that_run_commands_keep_to_commands_an_allowlist_can_cover():
    """#46: the coder, the tester and the reviewer read with the file tools and run one plain command per Bash call, never inline code,
    so a project's allow rules cover what they run and nothing asks the owner."""
    for role in ("coder", "tester", "reviewer"):
        text = " ".join((ROOT / "agents" / f"{role}.md").read_text(encoding="utf-8").split())
        assert "Read files with Read, Grep and Glob" in text, role
        assert "One plain command per Bash call; the only chaining is `cd <worktree> && <command>`" in text, role
        for banned in ("`python -`", "`python -c`", "heredoc scripts", "shell loops", "`base64`", "`awk`"):
            assert banned in text, (role, banned)


def test_the_command_and_the_plugin_say_the_same_version():
    """#40: `saani --version` prints the plugin's version, so the command on the PATH and `claude plugin list` agree."""
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "--version"], capture_output=True, text=True, encoding="utf-8")
    assert (r.stdout + r.stderr).strip() == f"saani {plugin['version']}"
