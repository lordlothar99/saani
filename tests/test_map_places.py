"""The map says what puts an issue in each place (#116): a "?" tooltip on every place's popup and a help popup listing them all.
The criteria are one table in map.html (`criteriaOf`); these tests keep it complete and wired."""
import pathlib
import re
import shutil
import subprocess

import pytest

PAGE = (pathlib.Path(__file__).resolve().parents[1] / "saani" / "map.html").read_text(encoding="utf-8")


def zone_ids():
    block = PAGE.split("const ZONES = [", 1)[1].split("\n];", 1)[0]
    return re.findall(r'\{id: "(\w+)"', block)


def criteria_ids():
    block = PAGE.split("function criteriaOf(id) {", 1)[1].split("}[id] || {};", 1)[0]
    return re.findall(r"^    (\w+): \{", block, re.M)


def test_every_place_has_criteria():
    assert zone_ids() and sorted(criteria_ids()) == sorted(zone_ids())


def test_a_place_without_issues_says_so_instead_of_listing_rules():
    block = PAGE.split("function criteriaOf(id) {", 1)[1].split("}[id] || {};", 1)[0]
    for place in ("camp", "outpost"):
        entry = re.search(r"^    %s: \{(.*?)\n(?=    \w+: \{|$)" % place, block, re.M | re.S).group(1)
        assert "note:" in entry and "rows:" not in entry


def test_the_popups_and_the_menu_are_wired():
    assert 'el("h2", {}, z.name, qmark(z))' in PAGE                              # the place popup
    assert "qmark(ZONE.tower)" in PAGE                                           # the release pull request popup
    assert 'id="places-help"' in PAGE and "openPanel(placesPanel())" in PAGE      # the "?" menu button and its popup
    assert "ZONES.filter(z => z.id !== \"camp\").concat(ZONE.camp)" in PAGE       # every place, in flow order, the camp last


def test_the_tooltip_closes_on_escape_before_the_panel_does():
    assert 'e.key === "Escape" && critBtn' in PAGE and "stopImmediatePropagation" in PAGE


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_script_parses(tmp_path):
    script = max(re.findall(r"<script[^>]*>(.*?)</script>", PAGE, re.S), key=len)
    js = tmp_path / "map.js"
    js.write_text(script, encoding="utf-8")
    done = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_map_and_every_popup_list_the_same_issues():
    """The Citadel leaves out the done issues of closed milestones on the map; its popup, the release PR's popup and the tower's tooltip
    take their cards from the same `zoneCards`, never from `DATA.cards` filtered by step again."""
    assert "const zoneCards = id => DATA.cards.filter(c => zoneOf(c) === id && shownCard(c));" in PAGE
    assert "if (shownCard(c)) (byZone[zoneOf(c)] ||= []).push(c)" in PAGE                      # the map's layout
    assert "const cards = zoneCards(z.id).sort(" in PAGE                                       # a place's popup
    assert 'cards = zoneCards("tower").sort(' in PAGE                                          # the release PR's popup
    assert not re.search(r'DATA\.cards\.filter\(c => \(?STEP_ZONE\[c\.step\]', PAGE)
