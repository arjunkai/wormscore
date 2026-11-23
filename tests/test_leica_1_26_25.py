"""1.26.25: a Leica zoom to the two decimals LAS X shows, and a white light laser's output.

A TCS SP5 recorded zoom "3" as 2.99875025381001, 2.99879313401932 and 2.99878455787935 in one session (Zenodo
7688944); a TCS SP8 records 1.21130609032365 and 1.21130795604721, both 1.21 in LAS X. The zoom is part of the
flat-field session, so such images were split into sessions of their own. LAS X shows the white light laser's
output as "WLL On, 70.00 %" (OutputPowerPercentage 0.7). A project scanned before reads as then."""
import xml.etree.ElementTree as ET

import numpy as np

from wormscore import checkreading as K
from wormscore import readers as R
from test_readers import HARDWARE, write_lif

WLL = '<LaserArray><Laser LaserName="WLL" PowerState="On" OutputPowerPercentage="0.7"/>' \
      '<Laser LaserName="405 Diode" PowerState="Off"/></LaserArray>'


def sp8(tmp_path, zoom="1.21130795604721", lasers=WLL):
    hw = HARDWARE.replace('Zoom="2"', f'Zoom="{zoom}"')
    hw = hw.replace("</ATLConfocalSettingDefinition>", lasers + "</ATLConfocalSettingDefinition>", 1)
    rng = np.random.default_rng(0)
    path = tmp_path / f"sp8_{zoom}_{len(lasers)}_{lasers.count('On')}.lif"      # one file per case
    write_lif(path, [("Series001", (rng.integers(0, 4096, (2, 6, 8)), rng.integers(0, 4096, (2, 6, 8))))], hardware=hw)
    return path


def test_the_zoom_is_read_to_two_decimals_as_las_x_shows_it(tmp_path):
    for raw, shown in (("1.21130795604721", "1.21"), ("1.21130609032365", "1.21"), ("2.99875025381001", "3"),
                       ("1.01143704321036", "1.01"), ("8", "8"), ("1.215", "1.22"), ("1.2149999", "1.21")):
        assert R.leica_images(sp8(tmp_path, raw), K.default_settings())[0]["zoom"] == shown, raw
    with R.as_1_26_24():
        assert R.leica_images(sp8(tmp_path), K.default_settings())[0]["zoom"] == "1.21130795604721"


def test_a_white_light_lasers_output_is_recorded_when_it_is_on(tmp_path):
    im = R.leica_images(sp8(tmp_path), K.default_settings())[0]
    assert im["scan_settings"] == "WLL 70%"
    assert im["settings"] == "HyD gain 100.0; 488nm@20.0%; line avg 3"          # the settings group stays
    off = R.leica_images(sp8(tmp_path, lasers=WLL.replace('PowerState="On"', 'PowerState="Off"')), K.default_settings())[0]
    assert "WLL" not in off.get("scan_settings", "")
    with R.as_1_26_24():
        assert "WLL" not in R.leica_images(sp8(tmp_path), K.default_settings())[0].get("scan_settings", "")


def test_an_odd_output_value_is_left_out_never_fatal():
    step = ET.fromstring('<S ScanSpeed="400"><LaserArray><Laser LaserName="WLL" PowerState="On" '
                         'OutputPowerPercentage="x"/></LaserArray></S>')
    assert R._leica_scan(step, None) == "400 Hz"
    master = ET.fromstring('<M><LaserArray><Laser LaserName="WLL" PowerState="On" OutputPowerPercentage="0.85"/>'
                           '</LaserArray></M>')
    assert R._leica_scan(ET.fromstring('<S ScanSpeed="600"/>'), master) == "600 Hz; WLL 85%"   # from the master


# ------------------------------------------------------------------ the Build card

from wormscore import flatfield as FF  # noqa: E402
from wormscore import rescan  # noqa: E402
from wormscore.files import read_csv  # noqa: E402
from test_lasaf import built, one_code, project, rewrite  # noqa: E402,F401  (fixtures)


def test_the_build_card_says_when_a_coded_leica_images_flat_field_zoom_changes(project, monkeypatch):
    import json
    cfg = json.loads((project / "config.json").read_text())
    cfg["flat_field"] = {"across_field": True}
    (project / "config.json").write_text(json.dumps(cfg))
    _, where = one_code(project)
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(scanned_with="1.26.24"))
    rewrite(project / "outputs" / "inventory.csv",
            lambda r: r.update(format="Leica LIF", zoom="1.21130795604721") if (r["local_path"], r["location_in_file"]) == where else None)
    row = next(r for r in read_csv(project / "outputs" / "inventory.csv") if (r["local_path"], r["location_in_file"]) == where)
    plain = " | ".join(x for x in (row["settings"], row["objective"]) if x)
    # the session the slide images were read with: the same image at its many-decimal zoom; the Zeiss mark set
    held = {"sessions": [{"system": row["system"], "settings": plain + " | 1.21130795604721"}], "scan_zoom": True}
    monkeypatch.setattr(FF, "summary", lambda project: held)
    # not scanned again: the old split is there, but nothing changed, so the card says nothing (found by review)
    assert not [x for x in rescan.scan_changes(project)["lines"] if "two decimals" in x]
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(scanned_with="1.26.25"))
    rewrite(project / "outputs" / "inventory.csv",
            lambda r: r.update(zoom="1.21") if (r["local_path"], r["location_in_file"]) == where else None)
    rescan._scan_changes.clear()
    changes = rescan.scan_changes(project)
    line = [x for x in changes["lines"] if "two decimals" in x]
    assert line == ["the flat-field zoom of 1 coded Leica image is now read to two decimals, as LAS X shows it; "
                    "choose the slide images again so theirs are read the same way"]
    assert changes["kinds"][changes["lines"].index(line[0])] == "slides"
    held["leica_zoom"] = True                       # the slide images were read the same way
    rescan._scan_changes.clear()
    assert not [x for x in rescan.scan_changes(project)["lines"] if "two decimals" in x]


def test_slide_images_are_read_with_two_decimals_only_after_a_new_scan(tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    for version, now in (("1.26.24", False), ("1.26.25", True)):
        (out / "inventory.csv").write_text(f"local_path,scanned_with\na.lif,{version}\n", encoding="utf-8")
        assert FF.leica_zoom_from_scan(tmp_path) == now, version
