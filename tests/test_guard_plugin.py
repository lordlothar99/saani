"""saani/guard.py as the plugin runs it: the hook's wiring in hooks/hooks.json, and `saani guard` with the event on stdin (the one place the
guard's tests start a process; the others call the hook in this process)."""
import json

from saani import guard
from guard_helpers import ROOT, fake_repo, hook_cli


def test_the_plugin_ships_the_hook_through_its_own_saani_command():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    (entry,) = hooks["hooks"]["PreToolUse"]
    assert entry["matcher"] == "*"
    assert entry["hooks"] == [{"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}/bin/saani" guard', "timeout": 15}]
    (after,) = hooks["hooks"]["PostToolUse"]
    assert after["matcher"] == "Bash" and after["hooks"] == entry["hooks"]
    launcher = (ROOT / "bin" / "saani").read_text()
    assert '"$@"' in launcher and "%*" in (ROOT / "bin" / "saani.cmd").read_text()
    assert "py -3" in launcher and "WindowsApps" in launcher and "sys.exit(sys.version_info" not in launcher


def test_the_command_prints_nothing_in_present_mode_and_a_deny_in_an_auto_session(tmp_path, home):
    main, work = fake_repo(tmp_path)
    assert hook_cli(home, work, "Bash", command="git push --force") is None
    assert hook_cli(home, work, "Bash", raw=b"not json") is None
    guard.turn_on("s-1", "run-1", main, tmp_path / "webshop-wt")
    assert hook_cli(home, work, "Bash", command="git status")["permissionDecision"] == "allow"
    out = hook_cli(home, work, "Bash", command="git push origin HEAD:dev")
    assert out["permissionDecision"] == "deny" and "saani land" in out["permissionDecisionReason"]
    assert hook_cli(home, work, "Bash", raw=b"{broken")["permissionDecision"] == "deny"
