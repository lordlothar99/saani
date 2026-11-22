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
    plugin = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
    assert (ROOT / entry["source"]).resolve() == (ROOT / "plugin").resolve()
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"])


def test_every_skill_has_a_name_and_a_description():
    for skill in (ROOT / "plugin" / "skills").glob("*/SKILL.md"):
        head = skill.read_text(encoding="utf-8").split("---")[1]
        assert re.search(r"^name: \S+", head, re.M), skill
        assert re.search(r"^description: .{20,}", head, re.M), skill


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


def test_the_board_sample_is_an_invented_project():
    sample = json.loads((ROOT / "saani" / "samples" / "board-sample.json").read_text(encoding="utf-8"))
    text = json.dumps(sample)
    assert "acme" in text or "webshop" in text


def test_the_command_starts_and_says_its_version():
    r = subprocess.run([sys.executable, "-I", str(ROOT / "saani.py"), "--version"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert re.search(r"\d+\.\d+", r.stdout + r.stderr)
