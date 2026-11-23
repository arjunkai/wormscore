"""1.26.23: Leica files saved by LAS AF (TCS SP5) have their objective and zoom read, as LAS X Office 1.4.11
shows them (22 November 2026, Fig6c.lif of Zenodo 13752242: Objective "HCX PL APO CS 20.0x0.70 DRY UV",
Zoom 2.4; Bio-Formats reads the same records). A project scanned before reads its slide and control images
without them until it scans again, and the Build card says what a rebuild would change."""
import csv
import json
import shutil

import numpy as np
import pytest

from wormscore import checkreading as K
from wormscore import flatfield as FF
from wormscore import readers as R
from wormscore import rescan
from wormscore.demo import make_demo
from wormscore.files import read_csv
from wormscore.pipeline import inventory, prepare
from test_readers import write_lif

# LAS AF writes no settings definition under the image's own attachment; its sequential block holds
# definitions with a placeholder objective, and the turret and scanner records hold the real values
LASAF = """ <Attachment Name="HardwareSetting">
  <ScannerSetting><ScannerSettingRecord Identifier="SystemType" Variant="TCS SP5"/>
   <ScannerSettingRecord Identifier="dblZoom" Description="Zoom" Variant="2.41743284242754"/></ScannerSetting>
  <FilterSetting><FilterSettingRecord ObjectName="DM6000 Turret" Attribute="Objective" Variant="HCX PL APO CS  20.0x0.70 DRY  UV"/></FilterSetting>
  <LDM_Block_Sequential><LDM_Block_Sequential_Master>
   <ATLConfocalSettingDefinition ObjectiveName="Dummy2" Zoom="1" LineAverage="4">
    <Spectro><MultiBand Channel="1" LeftWorld="500" RightWorld="550"/></Spectro>
    <AotfList><Aotf><LaserLineSetting LaserLine="488" IntensityDev="15"/></Aotf></AotfList>
    <DetectorList><Detector Name="PMT 1" Channel="1" Gain="1172" IsActive="1"/>
     <Detector Name="PMT Trans" Channel="2" Gain="200" IsActive="1"/></DetectorList>
   </ATLConfocalSettingDefinition></LDM_Block_Sequential_Master></LDM_Block_Sequential>
 </Attachment>"""


def lasaf_file(tmp_path):
    rng = np.random.default_rng(0)
    path = tmp_path / "sp5.lif"
    write_lif(path, [("Series009", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))],
              hardware=LASAF)
    return path


def test_lasaf_objective_and_zoom_are_read_as_las_x_shows_them(tmp_path):
    path = lasaf_file(tmp_path)
    im = R.leica_images(path, K.default_settings())[0]
    assert im["objective"] == "HCX PL APO CS  20.0x0.70 DRY  UV"       # not the placeholder "Dummy2"
    assert im["zoom"] == "2.41743284242754"                            # not the sequence's zoom 1
    assert im["model"] == "Leica TCS SP5"


def test_a_project_scanned_before_reads_them_as_before(tmp_path):
    path = lasaf_file(tmp_path)
    for gate in (R.as_1_26_22, R.as_1_26_20, R.as_before):
        with gate():
            im = R.leica_images(path, K.default_settings())[0]
        assert (im["objective"], im["zoom"]) == ("", ""), gate.__name__


def test_an_image_with_its_own_settings_is_read_as_before(tmp_path):
    """An SP8 or STELLARIS image (a settings definition of its own) is unchanged by the rule."""
    rng = np.random.default_rng(0)
    path = tmp_path / "sp8.lif"
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))])
    new = R.leica_images(path, K.default_settings())[0]
    with R.as_1_26_22():
        old = R.leica_images(path, K.default_settings())[0]
    assert new == old


def test_each_version_reads_at_its_own_level():
    import contextlib
    assert R._BEFORE.get() is False
    for version, level in (("1.26.19", "as_1_26_20"), ("1.26.20", "as_1_26_20"), ("1.26.21", "as_1_26_22"),
                           ("1.26.22", "as_1_26_22"), ("1.26.23", "as_1_26_23"), ("1.26.24", "current"),
                           ("1.27.0", "current")):
        gate = R.reading_as(version)
        if level == "current":
            assert isinstance(gate, contextlib.nullcontext), version
            continue
        with gate:
            assert R._before_1_26_24(), version
            assert R._before_1_26_23() == (level != "as_1_26_23"), version
            assert R._before_1_26_21() == (level == "as_1_26_20"), version


def test_check_reading_names_the_las_af_sections(tmp_path):
    text = K.check_reading(lasaf_file(tmp_path))
    where = text[text.index("Where to find these in Leica LAS X"):]
    assert "saved by LAS AF" in where
    rows = [line for line in where.split("\n\n")[0].splitlines()[2:]]
    assert rows and max(len(line) for line in rows) <= 72           # none wraps in the app's dialog
    assert "Hardware Settings, Objective" in where and "Scanner Settings, Zoom (one decimal)" in where
    assert "Total Exposures" in where


def test_check_reading_for_las_x_files_names_zoom_and_the_sequences(tmp_path):
    rng = np.random.default_rng(0)
    path = tmp_path / "sp8.lif"
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))])
    text = K.check_reading(path)
    where = text[text.index("Where to find these in Leica LAS X"):]
    assert "Confocal Settings, Zoom" in where and 'Setting "Seq. n"' in where
    assert "saved by LAS AF" not in where


# ------------------------------------------------------------------ the Build card

QUIET = {"log": lambda *a: None}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    project = tmp_path_factory.mktemp("lasaf")
    make_demo(project, n_per_group=2, **QUIET)
    inventory(project, **QUIET)
    prepare(project, **QUIET)
    return project


@pytest.fixture
def project(built, tmp_path):
    dst = tmp_path / "p"
    shutil.copytree(built, dst)
    rescan._scan_changes.clear()
    return dst


def rewrite(path, change):
    rows = read_csv(path)
    for r in rows:
        change(r)
    names = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=names, restval="")
        w.writeheader()
        w.writerows(rows)


def one_code(project):
    key = read_csv(project / "key" / "key.csv")
    return key[0]["code"], (key[0]["local_path"], key[0]["location_in_file"])


def test_an_objective_now_read_is_named_on_the_card(project):
    _, where = one_code(project)             # the demo's images name no objective, as LAS AF files did before
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(objective="HCX PL APO CS  63.0x1.40 OIL  UV")
            if (r["local_path"], r["location_in_file"]) == where else None)
    changes = rescan.scan_changes(project)
    assert changes["lines"] == ["the objective of 1 coded image is now read from the file (HCX PL APO CS 63.0x1.40 "
                                "OIL UV)"] and changes["kinds"] == ["rebuild"]


def test_a_coded_sp5_image_below_the_minimum_stays_in(project):
    """A coded image whose key records no objective (a TCS SP5 file before 1.26.23) is not left out when its
    objective, now read, is below the minimum: a project scored at 20x would otherwise be stopped (found by
    review). A rebuild with new codes applies the minimum to every image."""
    _, where = one_code(project)
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(objective="HCX PL APO CS  20.0x0.70 DRY  UV")
            if (r["local_path"], r["location_in_file"]) == where else None)
    changes = rescan.scan_changes(project)
    assert changes["lines"] == ["the objective of 1 coded image is now read from the file (HCX PL APO CS 20.0x0.70 "
                                "DRY UV); 1 below the minimum (40x) stays in, as it was coded before it was read"]
    assert changes["kinds"] == ["rebuild"]
    from wormscore.inclusion import include_reason, typed
    row = next(r for r in read_csv(project / "outputs" / "inventory.csv") if (r["local_path"], r["location_in_file"]) == where)
    cfg = C_load(project)
    assert include_reason(typed(row), cfg, coded=True) == ""
    assert include_reason(typed(row), cfg).startswith("objective is HCX PL APO CS  20.0x0.70 DRY  UV (minimum 40x)")


def test_a_coded_image_that_no_longer_passes_is_named_with_its_next_step(project):
    _, where = one_code(project)
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(marker_channel="", objective="HCX PL APO CS  63.0x1.40 OIL  UV")
            if (r["local_path"], r["location_in_file"]) == where else None)
    changes = rescan.scan_changes(project)
    left = [line for line in changes["lines"] if "inclusion rules" in line]
    assert left == ["1 coded image no longer passes the inclusion rules: GFP channel could not be identified from "
                    "metadata"], changes["lines"]
    assert changes["kinds"][changes["lines"].index(left[0])] == "left"
    (card,) = [x for x in rescan.scan_change_lines(project, changes) if x.startswith("1 coded image no longer")]
    assert card.endswith("Until every coded image passes again, a rebuild that keeps the codes, and Add new "
                         "images, would stop. If a setting caused this, setting it back lets the build go on. "
                         "Before scoring begins, Rebuild with NEW codes builds without them.")
    # no other line may then say a rebuild applies it without saying it would stop (found by review)
    (objective,) = [x for x in rescan.scan_change_lines(project, changes) if x.startswith("The objective")]
    assert objective.endswith("Once every coded image passes the inclusion rules again: rebuild, keeping the "
                              "current codes, to use it.")


def C_load(project):
    from wormscore import config as C
    return C.load(project)


def test_nothing_is_said_when_nothing_changed(project):
    assert rescan.scan_changes(project)["lines"] == []


def test_flat_field_line_stays_until_the_slide_images_are_read_the_same_way(project, monkeypatch):
    cfg = json.loads((project / "config.json").read_text())
    cfg["flat_field"] = {"across_field": True}
    (project / "config.json").write_text(json.dumps(cfg))
    _, where = one_code(project)
    rewrite(project / "outputs" / "inventory.csv",
            lambda r: r.update(format="Leica LIF", model="Leica TCS SP5", zoom="2.41743284242754")
            if (r["local_path"], r["location_in_file"]) == where else r.setdefault("model", ""))
    held = {"sessions": [{"system": "Leica", "settings": "x"}]}
    monkeypatch.setattr(FF, "summary", lambda project: held)
    changes = rescan.scan_changes(project)
    line = [x for x in changes["lines"] if "(LAS AF)" in x]
    assert line == ["the flat-field objective and zoom of 1 coded Leica image (LAS AF) are now read from the "
                    "file; choose the slide images again so theirs are read the same way"]
    assert changes["kinds"][changes["lines"].index(line[0])] == "slides"
    held["lasaf_objective"] = True
    rescan._scan_changes.clear()
    assert not [x for x in rescan.scan_changes(project)["lines"] if "(LAS AF)" in x]


def test_slide_images_are_read_with_the_new_rule_only_after_a_new_scan(project):
    assert FF.lasaf_from_scan(project)                      # scanned by this version
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(scanned_with="1.26.22"))
    assert not FF.lasaf_from_scan(project)


# ------------------------------------------------------------------ scan speed, pinhole, accumulation



def scan_file(tmp_path, speed="400"):
    from test_readers import HARDWARE
    hardware = HARDWARE.replace('LineAverage="3"', f'LineAverage="3" ScanSpeed="{speed}" PinholeAiry="1.00078" '
                                                   'FrameAverage="1" Line_Accumulation="3" FrameAccumulation="1"')
    rng = np.random.default_rng(0)
    path = tmp_path / f"scan{speed}.lif"
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))],
              hardware=hardware)
    return path


def test_scan_settings_are_read_into_their_own_field(tmp_path):
    im = R.leica_images(scan_file(tmp_path), K.default_settings())[0]
    assert im["scan_settings"] == "400 Hz; pinhole 1 AU; frame avg 1; line acc 3; frame acc 1"
    assert im["settings"] == "HyD gain 100.0; 488nm@20.0%; line avg 3"          # the settings text is as before
    with R.as_1_26_22():
        assert "scan_settings" not in R.leica_images(scan_file(tmp_path), K.default_settings())[0]


def test_check_reading_shows_them_and_where(tmp_path):
    text = K.check_reading(scan_file(tmp_path))
    assert "  Scan              400 Hz; pinhole 1 AU; frame avg 1; line acc 3; frame acc 1" in text
    assert "Confocal Settings, by the same names" in text


def test_an_image_scanned_otherwise_than_its_settings_group_is_flagged(tmp_path, monkeypatch):
    from wormscore import pipeline
    project = make_demo(tmp_path / "p", n_per_group=2, **QUIET)
    real = R.images_in
    seen = []

    def images_in(path, cfg, source=None):
        found = real(path, cfg, source)
        for img in found:
            seen.append(img)
            img.update(system="Leica TCS SP8", settings="HyD gain 100.0; 488nm@20.0%; line avg 3",
                       scan_settings="600 Hz; pinhole 1 AU" if len(seen) == 1 else "400 Hz; pinhole 1 AU")
        return found
    monkeypatch.setattr(R, "images_in", images_in)
    pipeline.inventory(project, **QUIET)
    rows = read_csv(project / "outputs" / "inventory.csv")
    flagged = [r for r in rows if "scan settings differ" in r["flags"]]
    assert len(rows) > 2 and len(flagged) == 1
    assert "scan settings differ from most images with the same settings (600 Hz here; most: 400 Hz)" \
        in flagged[0]["flags"]
    assert {r["settings"] for r in rows} == {"HyD gain 100.0; 488nm@20.0%; line avg 3"}   # one group, as before


def flags_for(tmp_path, monkeypatch, values):
    """Scan data on the demo with every image in one settings group and these scan settings, in turn."""
    from wormscore import pipeline
    project = make_demo(tmp_path / "p", n_per_group=2, **QUIET)
    real, seen = R.images_in, []

    def images_in(path, cfg, source=None):
        found = real(path, cfg, source)
        for img in found:
            img.update(system="Leica TCS SP8", settings="HyD gain 100.0; 488nm@20.0%; line avg 3",
                       scan_settings=values[len(seen) % len(values)])
            seen.append(img)
        return found
    monkeypatch.setattr(R, "images_in", images_in)
    pipeline.inventory(project, **QUIET)
    return [r["flags"] for r in read_csv(project / "outputs" / "inventory.csv")]


def test_a_value_an_image_does_not_record_is_not_a_difference(tmp_path, monkeypatch):
    flags = flags_for(tmp_path, monkeypatch, ["400 Hz; pinhole 1 AU", "400 Hz", "400 Hz; pinhole 1 AU"])
    assert not any("scan settings differ" in f for f in flags)


def test_an_even_split_is_not_flagged(tmp_path, monkeypatch):
    flags = flags_for(tmp_path, monkeypatch, ["400 Hz", "600 Hz"])
    assert len(flags) % 2 == 0 and not any("scan settings differ" in f for f in flags)


def test_the_objective_rule_is_for_las_af_files_only(tmp_path):
    """An image with no settings definition of its own in a file that names no LAS AF model keeps its
    objective and zoom as before (found by review)."""
    rng = np.random.default_rng(0)
    path = tmp_path / "nomodel.lif"
    write_lif(path, [("Series009", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))],
              hardware=LASAF.replace('<ScannerSettingRecord Identifier="SystemType" Variant="TCS SP5"/>', ""))
    im = R.leica_images(path, K.default_settings())[0]
    assert (im["objective"], im["zoom"]) == ("", "") and "model" not in im


def test_a_rebuild_keeping_the_codes_keeps_the_coded_sp5_image_and_says_so(project):
    """Through the real build: the coded image whose objective is now read as 20x stays in the blind set with
    its code (the build does not stop), the Build log says why, and a rebuild with new codes leaves it out."""
    from wormscore.pipeline import prepare
    code, where = one_code(project)
    before = sorted(r["code"] for r in read_csv(project / "key" / "key.csv"))
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(objective="HCX PL APO CS  20.0x0.70 DRY  UV")
            if (r["local_path"], r["location_in_file"]) == where else None)
    said = []
    prepare(project, log=said.append)
    assert sorted(r["code"] for r in read_csv(project / "key" / "key.csv")) == before
    warning = ("Warning: 1 coded image is kept although its objective is below the minimum (40x): the minimum "
               "decides which images are coded, and a rebuild with new codes applies it.")
    assert warning in said, said
    rescan._scan_changes.clear()
    assert rescan.scan_changes(project)["lines"] == []      # the key now records it: nothing more to say
    said.clear()
    prepare(project, log=said.append)                       # and every later build keeps it too
    assert warning in said and sorted(r["code"] for r in read_csv(project / "key" / "key.csv")) == before
    prepare(project, reassign=True, log=said.append)
    keyed = {(r["local_path"], r["location_in_file"]) for r in read_csv(project / "key" / "key.csv")}
    assert where not in keyed
    assert any(r.get("local_path") == where[0] and r["reason"].startswith("objective is")
               for r in read_csv(project / "outputs" / "excluded.csv"))


def test_the_methods_draft_says_when_coded_images_below_the_minimum_were_kept(project):
    from wormscore import methods
    from wormscore import config as C
    rows = read_csv(project / "key" / "key.csv")
    rows[0]["objective"] = "HCX PL APO CS  20.0x0.70 DRY  UV"
    cfg = C.load(project)
    assert methods.kept_below_minimum(project, cfg, rows) == (
        ". 1 coded image has an objective below the minimum magnification (40x): its objective was read, or the "
        "minimum raised, only after it was coded, and it kept its code")
    # a project scanned before 1.26.23 reports as before
    rewrite(project / "outputs" / "inventory.csv", lambda r: r.update(scanned_with="1.26.22"))
    assert methods.kept_below_minimum(project, cfg, rows) == ""


def test_several_left_lines_say_the_step_once_and_a_reason_ends_cleanly():
    changes = {"lines": ["2 coded images no longer pass the inclusion rules: excluded by config rule 'x': blurry",
                         "1 coded image no longer passes the inclusion rules: time series (3 time points)"],
               "kinds": ["left", "left"], "channel_codes": {}}
    import wormscore.flatfield as FF
    real = FF.lock_reason
    FF.lock_reason = lambda project: "scoring has begun"
    try:
        first, second = rescan.scan_change_lines("unused", changes)
    finally:
        FF.lock_reason = real
    assert first == "2 coded images no longer pass the inclusion rules: excluded by config rule 'x': blurry."
    assert second.endswith("time points). Until every coded image passes again, a rebuild that keeps the codes, "
                           "and Add new images, would stop. If a setting caused this, setting it back lets the build go on.")
