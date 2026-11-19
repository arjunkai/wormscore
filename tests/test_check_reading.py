"""wormscore check-reading FILE: what wormscore reads from one microscope file, as plain text, to hold
against what the microscope software shows. It reads metadata only, goes through readers.images_in (so it
shows what Scan data would write to inventory.csv) and writes nothing anywhere."""
import os
from pathlib import Path

import numpy as np
import pytest

from test_acquisition_settings import write_ome
from test_olympus_oir import stack, write_oir
from test_reader_items import rewrite
from test_readers import write_lif
from wormscore import checkreading as K
from wormscore import cli, readers
from wormscore.demo import make_demo
from wormscore.pipeline import inventory

quiet = lambda *a: None
NAMES = ("Series001", "Series002", "Position 1/Series003")


def lif(path, names=NAMES):
    rng = np.random.default_rng(0)
    write_lif(path, [(n, (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8)))) for n in names])
    return path


def listing(folder):
    return sorted((str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*"))


def value(text, label):
    """The text after a label at the start of a line of the report (the first such line: the values come
    before the lines that say where the software shows them)."""
    line = next(ln for ln in text.splitlines() if ln.strip().startswith(label))
    return line.strip()[len(label):].strip()


def test_one_leica_image_lists_what_scan_data_reads(tmp_path):
    text = K.check_reading(lif(tmp_path / "one.lif", NAMES[:1]))
    assert "Leica LIF" in text and "series 0 (Series001)" in text
    assert value(text, "Microscope") == "Leica TCS TEST"
    assert value(text, "Objective") == "HC PL APO 63x/1.40 OIL"
    assert value(text, "Pixel size") == "0.25 um" and value(text, "Z-step") == "0.8 um"
    assert value(text, "Planes") == "4" and value(text, "Bit depth") == "12"
    assert value(text, "Green") == "reporter (GFP)" and value(text, "Gray") == "brightfield"
    assert value(text, "Detector gain") == "100.0 (HyD)"
    assert value(text, "Laser 488 nm") == "20.0%"
    assert value(text, "Averaging") == "line avg 3"
    assert "HyD gain 100.0; 488nm@20.0%; line avg 3" in text         # the settings text exactly as in inventory.csv
    assert "Leica LAS X" in text and "Properties" in text
    assert "This file holds" not in text                              # one image: no list
    assert "—" not in text and "Traceback" not in text


def test_leica_sp8_gain_says_its_unit_as_las_x_shows_it(tmp_path):
    """On a TCS SP8, LAS X shows a HyD's gain in percent (10 to 500 %, 100 % when Live starts) and a PMT's in
    volts (0 to 1250 V). The file holds the number alone; inventory.csv keeps it as before ("HyD gain 458.3").
    On a STELLARIS the HyD S detectors record in photon counting, where LAS X turns the gain off and the
    file still holds a number such as 2.5 (a real STELLARIS 5 file): no unit is claimed for other systems."""
    path = lif(tmp_path / "one.lif", NAMES[:1])
    text = K.check_reading(path)
    assert value(text, "Detector gain") == "100.0 (HyD)"                 # the test file's "Leica TCS TEST"
    cfg = K.default_settings()
    img = K._images(path, cfg, None)[0]
    for system, settings, shown in (("Leica TCS SP8", "HyD gain 458.3; 488nm@2.0%", "458.3 % (HyD)"),
                                    ("Leica TCS SP8", "PMT gain 625.0; 488nm@2.0%", "625.0 V (PMT)"),
                                    ("Leica TCS SP8 X", "APD gain 3.0", "3.0 (APD)"),
                                    ("Leica STELLARIS 5", "HyD gain 2.5; 488nm@1.0%", "2.5 (HyD)"),
                                    ("Leica TCS SP5", "PMT gain 100", "100 (PMT)")):
        lines = K.describe(dict(img, system=system, settings=settings), cfg, path)
        assert value("\n".join(lines), "Detector gain") == shown, system
        assert value("\n".join(lines), "In inventory.csv") == settings


def test_leica_start_time_is_shown_as_las_x_and_bio_formats_read_it(tmp_path, monkeypatch):
    """LAS X Office's Properties window shows "Start Time: 10/15/2026 4:20:58.187 PM" for Series001 of the
    owner's SP8 file; the reading said "not recorded in the file". LAS X writes each frame's time as a
    Windows file time in hexadecimal (UTC) in the series' TimeStampList; LAS X shows the first in the
    viewing computer's time zone, and Bio-Formats gives 2026-10-15T20:20:58 for the same file. Scan data
    records it too (test_leica_dates.py); here in UTC."""
    import datetime as dt
    from test_readers import HARDWARE
    monkeypatch.setattr(readers, "LOCAL_ZONE", dt.timezone.utc)
    stamps = '<TimeStampList NumberOfTimeStamps="2">1dd5ce2b05779b0 1dd5ce2b2a73610 </TimeStampList>'
    rng = np.random.default_rng(0)
    path = tmp_path / "dated.lif"
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))],
              hardware=HARDWARE + stamps)
    text = K.check_reading(path)
    assert value(text, "Acquired") == "2026-10-15 20:20:58 +00:00"
    assert readers.leica_images(path, K.default_settings())[0]["acquired"] == "2026-10-15 20:20:58 +00:00"
    where = text[text.index("Where to find these"):].splitlines()
    assert where[2].strip() == "Acquired                  Start Time (this computer's time zone)"   # first, as above
    undated = K.check_reading(lif(tmp_path / "undated.lif", NAMES[:1]))
    assert value(undated, "Acquired") == "not recorded in the file"
    assert "Start Time" not in undated                             # no place for a value the file lacks
    # older LAS X files write each time as <TimeStamp HighInteger= LowInteger=>, which Bio-Formats reads too
    old = ('<TimeStampList><TimeStamp HighInteger="30758066" LowInteger="1592197632"/>'
           '<TimeStamp HighInteger="30758066" LowInteger="1692197632"/></TimeStampList>')
    path = tmp_path / "old.lif"
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))],
              hardware=HARDWARE + old)
    assert value(K.check_reading(path), "Acquired") == "2019-08-17 04:15:15 +00:00"
    broken = '<TimeStampList NumberOfTimeStamps="1">zz</TimeStampList>'
    path = tmp_path / "broken.lif"
    write_lif(path, [("Series001", (rng.integers(0, 4096, (4, 6, 8)), rng.integers(0, 4096, (4, 6, 8))))],
              hardware=HARDWARE + broken)
    assert value(K.check_reading(path), "Acquired") == "not recorded in the file"


def test_leica_lines_name_the_sections_of_the_properties_window(tmp_path):
    """Names as LAS X Office 1.4.11 shows them for a TCS SP8 file (19 November 2026): Dimensions (Logical
    Size, Pixel Size / Voxel Size), Channels (LUT, Resolution), Confocal Settings (ObjectiveName,
    LineAverage), Laser Lines (Intensity) and Detectors (Gain, a number with no unit)."""
    text = K.check_reading(lif(tmp_path / "one.lif", NAMES[:1]))
    where = text[text.index("Where to find these in Leica LAS X"):]
    assert value(where, "Objective") == "Confocal Settings, ObjectiveName"
    assert value(where, "Pixel size") == "Dimensions, Pixel Size / Voxel Size, X"
    assert value(where, "Z-step") == "Dimensions, Pixel Size / Voxel Size, Z"
    assert value(where, "Planes") == "Dimensions, Logical Size, Z"
    assert value(where, "Bit depth") == "Channels, Resolution"
    assert value(where, "Channels") == "Channels, LUT"
    assert value(where, "Detector gain or voltage") == "Detectors, the Active one's Gain (no % or V)"
    assert value(where, "Laser lines and power") == "Laser Lines, Intensity"
    assert value(where, "Averaging") == "Confocal Settings, LineAverage"
    rows = where.split("\n\n")[0].splitlines()[2:]                       # the lines that name a place
    assert len(rows) == 9 and max(len(line) for line in rows) <= 72      # none wraps in the app's dialog


def test_nothing_is_written_anywhere(tmp_path, monkeypatch):
    home_dir, data = tmp_path / "home", tmp_path / "data"
    project = make_demo(tmp_path / "project", n_per_group=1, log=quiet)
    home_dir.mkdir()
    data.mkdir()
    for var in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(home_dir))
    monkeypatch.chdir(home_dir)
    files = [lif(data / "a.lif"), write_ome(data / "b.ome.tif", gain=2, laser=0.5, bits=16),
             write_oir(data / "c.oir", stack()), data / "broken.czi"]
    files[-1].write_bytes(b"not a czi")
    before = listing(tmp_path)
    for f in files:
        assert K.text_for(f) and K.text_for(f, project=project)
    assert cli.main(["check-reading", str(files[0]), "--series", "1", "--project", str(project)]) is None
    assert listing(tmp_path) == before
    assert os.listdir(home_dir) == []


def test_no_pixel_plane_is_read(tmp_path, monkeypatch):
    path = lif(tmp_path / "a.lif")
    ome = write_ome(tmp_path / "b.ome.tif", bits=16)

    def never(*a, **k):
        raise AssertionError("pixels were read")
    monkeypatch.setattr(readers, "_raw", never)
    monkeypatch.setattr(readers, "read", never)
    monkeypatch.setattr(readers, "pixel_sha256", never)
    monkeypatch.setattr(readers.tifffile, "imread", never)
    assert "Leica LIF" in K.check_reading(path) and "OME-TIFF" in K.check_reading(ome)


def test_formula_like_text_keeps_its_leading_mark(tmp_path):
    path = write_ome(tmp_path / "a.ome.tif", gain=2, bits=16)
    rewrite(path, lambda xml: xml.replace('Name="Brightfield"', 'Name="=HYPERLINK(1)"').replace(
        "<Image ", '<Instrument ID="Instrument:0"><Microscope Model="=cmd"/></Instrument><Image ', 1))
    text = K.check_reading(path)
    assert value(text, "Microscope") == "'=cmd"
    assert value(text, "'=HYPERLINK(1)") == "not used"
    assert not any(ln.strip().startswith("=") for ln in text.splitlines())


def test_it_reports_what_scan_data_writes_to_the_inventory(tmp_path):
    """The same file through Scan data: every value of the report is the inventory row's."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    for f in (project / "data").glob("*.tif"):
        f.unlink()
    path = write_ome(project / "data" / "demo_control_01.ome.tif", gain=2.5, laser=0.5)      # no SignificantBits: a flag
    text = K.check_reading(path, project=project)
    (row,) = inventory(project, log=quiet)
    assert value(text, "Microscope") == row["system"]
    assert value(text, "Pixel size") == f"{row['pixel_um']} um" and value(text, "Z-step") == f"{row['z_step_um']} um"
    assert value(text, "Planes") == str(row["n_z"]) and value(text, "Bit depth") == str(row["bits"])
    assert row["settings"] and row["settings"] in text
    assert row["flags"] and all(flag in text for flag in row["flags"].split("; "))


def test_flags_scan_data_would_raise_are_shown(tmp_path):
    path = write_ome(tmp_path / "a.ome.tif", bits=16)
    rewrite(path, lambda xml: xml.replace('Name="GFP"', 'Name="Cy5"').replace('Name="Brightfield"', 'Name="Cy3"'))
    text = K.check_reading(path)
    assert "GFP channel UNRESOLVED" in text and "no brightfield channel identified" in text
    assert value(text, "Cy5") == "not used"
    assert "no single reporter channel" in text                      # so no settings are listed
    clean = K.check_reading(write_ome(tmp_path / "b.ome.tif", bits=16))
    assert "Flags Scan data would raise: none" in clean


def test_several_series_are_listed_and_one_is_picked(tmp_path):
    path = lif(tmp_path / "three.lif")
    text = K.check_reading(path)
    assert "This file holds 3 images" in text
    for k, name in enumerate(NAMES):
        assert f"{k}  series {k} ({name})" in text
    assert "Leica LIF, series 0 (Series001)" in text and "--series" in text
    second = K.check_reading(path, series=2)
    assert "Leica LIF, series 2 (Position 1/Series003)" in second and "Leica LIF, series 0" not in second
    with pytest.raises(K.NotRead, match="no image 7.*0 to 2"):
        K.check_reading(path, series=7)


def test_an_unreadable_file_gives_a_plain_reason(tmp_path, capsys):
    bad = tmp_path / "broken.oir"
    bad.write_bytes(b"OLYMPUSRAWFORMAT" + bytes(16) + (10**9).to_bytes(8, "little") + bytes(8))
    text = K.text_for(bad)
    assert "broken.oir" in text and "the file is incomplete" in text and "Traceback" not in text
    assert "not found" in K.text_for(tmp_path / "missing.lif")
    assert "folder" in K.text_for(tmp_path)
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    assert "reference (not an image)" in K.text_for(notes)
    junk = tmp_path / "junk.czi"
    junk.write_bytes(b"not a czi")
    assert "could not read junk.czi" in K.text_for(junk) and "Traceback" not in K.text_for(junk)
    assert "config.json" in K.text_for(lif(tmp_path / "a.lif"), project=tmp_path / "no project")
    with pytest.raises(SystemExit) as stop:                           # the command: the reason, and a failing exit
        cli.main(["check-reading", str(bad)])
    assert "the file is incomplete" in str(stop.value.code)


def test_the_project_sets_the_markers(tmp_path):
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet, identity=True)
    path = write_ome(tmp_path / "a.ome.tif", bits=16)
    rewrite(path, lambda xml: xml.replace('Name="Brightfield"', 'Name="mCherry"'))
    assert value(K.check_reading(path), "mCherry") == "not used"
    text = K.check_reading(path, project=project)
    assert value(text, "mCherry").startswith("identity channel") and "no brightfield channel identified" in text


def test_an_imagej_tiff_needs_the_project_that_states_its_channels(tmp_path):
    import json
    import tifffile
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    cfg = json.loads((project / "config.json").read_text(encoding="utf-8"))
    cfg["sources"][0]["imagej_tiff"] = {"channels": ["marker", "brightfield"]}
    (project / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    path = project / "data" / "stack.tif"
    tifffile.imwrite(path, np.zeros((3, 2, 8, 8), "uint16"), imagej=True, resolution=(5, 5),
                     metadata={"axes": "ZCYX", "unit": "um", "spacing": 0.5})
    text = K.check_reading(path, project=project)
    assert "ImageJ TIFF" in text and "acquisition software" in text and "ImageJ" in value(text, "Written by")
    assert value(text, "marker") == "reporter (GFP)" and value(text, "Pixel size") == "0.2 um"
    with pytest.raises(K.NotRead, match="ImageJ"):
        K.check_reading(path)


def test_an_ome_tiff_names_the_software_that_wrote_it(tmp_path):
    text = K.check_reading(write_ome(tmp_path / "a.ome.tif", bits=16))
    assert "no microscope vendor's panel" in text and "tifffile" in value(text, "Written by")


def test_the_command(tmp_path, capsys):
    path = lif(tmp_path / "three.lif")
    cli.main(["check-reading", str(path), "--series", "1"])
    out = capsys.readouterr().out
    assert "Leica LIF, series 1 (Series002)" in out
    assert out.rstrip("\n") == K.check_reading(path, series=1)


def test_the_home_page_shows_the_reading_of_a_chosen_file(tmp_path, monkeypatch):
    """The Windows app has no console: Check the reading... next to Scan data picks a file and shows the text."""
    import http.client
    import json
    import threading
    from wormscore import home
    monkeypatch.setattr(home, "SETTINGS", tmp_path / "settings.json")
    path, asked = lif(tmp_path / "three.lif"), []
    app = home.HomeApp(open_windows=False, pick=lambda *a, **k: asked.append((a, k)) or str(path))
    srv = home.make_home_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=60)
        conn.request("POST", "/api/check-reading", body="{}", headers={"Content-Type": "application/json"})
        got = json.loads(conn.getresponse().read())
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()
    assert got["text"] == K.check_reading(path) and asked[0][1] == {"file": True}
    js = (Path(home.__file__).parent / "static" / "home.js").read_text(encoding="utf-8")
    assert "Check the reading..." in js and '"/api/check-reading"' in js
