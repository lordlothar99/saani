"""The map says what puts an issue in each place (#116): a "?" tooltip on every place's popup and a help popup listing them all.
The words are the server's profile table (saani/profile.py, #123): the map reads them from the state (and the sample), with no copy of
its own; these tests keep the table complete and wired."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from saani import config, profile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = (ROOT / "saani" / "map.html").read_text(encoding="utf-8")
SAMPLE = json.loads((ROOT / "saani" / "samples" / "map-sample.json").read_text(encoding="utf-8"))


def zone_ids():
    block = PAGE.split("const ZONES = [", 1)[1].split("\n];", 1)[0]
    return re.findall(r'\{id: "(\w+)"', block)


def criteria_block():
    return PAGE.split("function criteriaOf(id) {", 1)[1].split("\n}", 1)[0]


def test_every_place_has_criteria():
    """Every place of the map that holds issues has a profile, in the map's flow order; the Hollow (camp) holds none."""
    assert [z for z in zone_ids() if z != "camp"] == list(profile.IDS)
    places = profile.describe()["places"]
    assert [p["id"] for p in places] == list(profile.IDS) and all(p["rows"] for p in places)


def test_the_map_keeps_no_copy_of_the_rules():
    """criteriaOf reads the state's profile; the old hand-written rows and the old "which place wins" list are gone."""
    block = criteria_block()
    assert "PROFILE().places.find(x => x.id === id)" in block and "rows:" not in block.replace("rows: p.rows", "")
    assert "const first = PROFILE().wins" in PAGE
    for gone in ("none yet: `saani start` has not been run on it", "Its tests are running: The Proving Ground", "oldCriteriaOf"):
        assert gone not in PAGE


def test_a_place_without_issues_says_so_instead_of_listing_rules():
    assert 'if (id === "camp") return {note: CAMP_NOTE};' in criteria_block() and "No issue lives here." in PAGE


def test_the_far_outpost_has_real_rows_and_the_board_column_maps_to_it():
    outpost = next(p for p in profile.describe()["places"] if p["id"] == "outpost")
    assert any("Live checks" in title for title, _ in outpost["rows"]) and "Live checks" in json.dumps(outpost["rows"])
    assert profile.COLUMN["outpost"] == "checks" and 'outpost: "checks"' in PAGE.split("const STEP_OF_PLACE", 1)[1].split("};", 1)[0]


def test_the_sample_carries_the_profile_the_server_gives_the_invented_project():
    """The published map reads the sample: its rules are the profile of the invented project's defaults (regenerate it when the profile
    changes: the `profile` of the sample is `profile.describe(Config(repo="acme/webshop"), statuses_on=True)`)."""
    assert SAMPLE["profile"] == json.loads(json.dumps(profile.describe(config.Config(repo="acme/webshop"), statuses_on=True)))


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


def test_every_place_s_step_is_drawn_in_its_own_zone():
    """The step normalize() gives a place is drawn in the zone of the same place."""
    steps = dict(re.findall(r'(\w+): "(\w+)"', PAGE.split("const STEP_OF_PLACE = {", 1)[1].split("};", 1)[0]))
    zones = dict(re.findall(r'(\w+): "(\w+)"', PAGE.split("const STEP_ZONE = {", 1)[1].split("};", 1)[0]))
    assert set(steps) == set(profile.IDS) and all(zones[steps[p]] == p for p in profile.IDS)
    assert zones["started"] == "works"
