"""The reporter and identity channel, chosen in Project settings (1.24.0)."""
import json
import os
import time

import pytest

from wormscore import editconfig as E
from wormscore import markers as MK
from wormscore.home import project_status
from wormscore.pipeline import inventory


def body(project, **markers):
    cur = E.current(project)
    m = {k: cur["markers"][k] for k in ("reporter", "identity", "spots_from")} | markers
    return {"project_name": cur["project_name"], "sources": [dict(s, **{"from": i}) for i, s in enumerate(cur["sources"])],
            "markers": m}


def raw_of(project):
    return json.loads((project / "config.json").read_text(encoding="utf-8"))


def test_the_template_marker_reads_as_gfp_and_an_unchanged_save_writes_nothing(fresh_project):
    before = raw_of(fresh_project)
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    m = E.current(fresh_project)["markers"]
    assert (m["reporter"], m["identity"], m["spots_from"], m["locked"]) == ("GFP", "", "identity", False)
    assert "NeuroPAL" not in m["presets"]           # offered as its own choice, not a single preset
    r = E.save(fresh_project, body(fresh_project))
    assert not r["markers_changed"]
    after = raw_of(fresh_project)
    assert after.get("marker") == before.get("marker") and "identity" not in after
    assert (fresh_project / "log.md").read_text(encoding="utf-8") == log
    assert project_status(fresh_project)["markers_stale"] is None


@pytest.mark.parametrize("ident, want", [
    (None, ("", "identity")),
    ({"preset": "mCherry"}, ("mCherry", "identity")),
    ({"preset": "mcherry", "spots_from": "reporter"}, ("mCherry", "reporter")),
    ({"preset": "NeuroPAL", "spots_from": "reporter"}, ("NeuroPAL", "reporter")),
    ({"preset": "NeuroPAL", "label": "Mine"}, ("custom", "identity")),
    ({"preset": "mCherry", "color": [1, 1, 1]}, ("custom", "identity")),
    ({"dye_names": ["Mine"]}, ("custom", "identity")),
    ([{"preset": "mCherry"}, {"preset": "BFP"}], ("custom", "identity")),
])
def test_identity_choices_are_read_back(ident, want):
    assert MK.identity_choice({"identity": ident} if ident is not None else {}) == want


def test_a_changed_reporter_color_reads_as_custom_and_is_kept():
    raw = {"marker": {"preset": "GFP", "color": [0, 1, 1]}}
    assert MK.reporter_choice(raw) == "custom"
    out, lines = MK.change(".", raw, dict(raw), {"reporter": "custom", "identity": "", "spots_from": "identity"})
    assert out == raw and lines == []


def test_saved_choices_are_written_logged_and_ask_for_scan_then_rebuild(fresh_project):
    r = E.save(fresh_project, body(fresh_project, reporter="mNeonGreen", identity="mCherry", spots_from="reporter"))
    assert r["markers_changed"]
    raw = raw_of(fresh_project)
    assert raw["marker"] == {"preset": "mNeonGreen"}
    assert raw["identity"] == {"preset": "mCherry", "spots_from": "reporter"}
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert "markers changed in Project settings" in log and "Reporter: GFP -> mNeonGreen." in log
    assert "Identity channel: none -> mCherry." in log
    m = E.current(fresh_project)["markers"]
    assert (m["reporter"], m["identity"], m["spots_from"]) == ("mNeonGreen", "mCherry", "reporter")
    assert project_status(fresh_project)["markers_stale"] == {"scan": True, "build": True}

    later = time.time() + 5
    os.utime(fresh_project / "outputs" / "inventory.csv", (later, later))    # as after Scan data
    assert MK.stale(fresh_project) == {"scan": False, "build": True}
    os.utime(fresh_project / "key" / "key.csv", (later, later))             # as after a rebuild
    assert MK.stale(fresh_project) is None

    E.save(fresh_project, body(fresh_project, reporter="GFP", identity=""))
    raw = raw_of(fresh_project)
    assert "identity" not in raw and raw["marker"] == {"preset": "GFP"}


def test_neuropal_is_written_as_its_preset(fresh_project):
    E.save(fresh_project, body(fresh_project, identity="NeuroPAL"))
    assert raw_of(fresh_project)["identity"] == {"preset": "NeuroPAL"}
    assert E.current(fresh_project)["markers"]["identity"] == "NeuroPAL"


def test_the_first_scan_after_a_change_clears_the_flag(fresh_project):
    (fresh_project / "outputs" / "inventory.csv").unlink()
    E.save(fresh_project, body(fresh_project, reporter="YFP"))
    assert not (fresh_project / "outputs" / "channels_changed.json").exists()
    E.save(fresh_project, body(fresh_project, reporter="GFP"))
    inventory(fresh_project, log=lambda *a: None)
    assert MK.stale(fresh_project) is None


@pytest.mark.parametrize("markers, message", [
    ({"reporter": "TagRFP", "identity": "NeuroPAL"}, "also the reporter"),
    ({"reporter": "GFP", "identity": "GFP"}, "also the reporter"),
    ({"reporter": "Nope"}, "reporter from the list"),
    ({"identity": "custom"}, "identity channel from the list"),
    ({"identity": "mCherry", "spots_from": "both"}, "suggested cells"),
])
def test_bad_choices_are_refused_and_nothing_is_written(fresh_project, markers, message):
    before = (fresh_project / "config.json").read_bytes()
    with pytest.raises(ValueError, match=message):
        E.save(fresh_project, body(fresh_project, **markers))
    assert (fresh_project / "config.json").read_bytes() == before


def test_markers_lock_once_scoring_starts_and_change_anyway_records_the_reason(fresh_project):
    (fresh_project / "scoring").mkdir(exist_ok=True)
    (fresh_project / "scoring" / "scores.json").write_text("{}")
    assert E.current(fresh_project)["markers"]["locked"]
    E.save(fresh_project, body(fresh_project))                  # unchanged: saves as before
    with pytest.raises(ValueError, match="Change anyway"):
        E.save(fresh_project, body(fresh_project, identity="mCherry"))
    E.save(fresh_project, body(fresh_project, identity="mCherry", unlock_reason="  set up\nwrongly "))
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert MK.CHANGE_TITLE in log and "Reason given: set up wrongly" in log
    assert raw_of(fresh_project)["identity"] == {"preset": "mCherry"}
