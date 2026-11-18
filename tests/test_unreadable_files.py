"""1.26.5: a file that cannot be read is left out with the reason. It never stops Scan data or Build,
and its missing planes are never shown as black."""
from pathlib import Path

import pytest

from wormscore import config as C
from wormscore import readers
from wormscore.demo import make_demo
from wormscore.pipeline import include_reason, inventory, prepare, read_csv

quiet = lambda *a: None
STATIC = Path(readers.__file__).parent / "static"


def image(**over):
    return {"source": "s", "relative_path": "a.czi", "marker_channel": 0, "objective": "63x/1.4 Oil", "n_t": 1,
            "n_z": 20, "z_step_um": 1.0, "pixel_um": 0.2, "flags": "", **over}


def test_decimal_magnification_is_read_whole():
    """'63.0x' was read as 0x (the digits before the x), so every such image was left out."""
    cfg = C.merge(C.DEFAULTS, {})
    assert include_reason(image(objective="63.0x/1.4 Oil"), cfg) == ""
    assert include_reason(image(objective="HC PL APO CS2 63.0x1.40 OIL"), cfg) == ""
    assert include_reason(image(objective="100.0x/1.4 Oil"), cfg) == ""
    assert "minimum 40x" in include_reason(image(objective="C PLAN  10.0x0.22 POL"), cfg)
    assert "minimum 40x" in include_reason(image(objective="20x/0.8"), cfg)
    assert include_reason(image(objective=""), cfg) == ""


def test_images_build_cannot_use_are_left_out_with_the_reason():
    cfg = C.merge(C.DEFAULTS, {})
    assert "could not be read" in include_reason(image(flags="pixels could not be read (ValueError)"), cfg)
    assert "pixel size" in include_reason(image(pixel_um=0.0), cfg)


def test_an_ome_tiff_cut_short_is_flagged_and_left_out(tmp_path):
    """tifffile reads the missing planes of a cut file as zeros, so it was coded as a normal image with
    black planes: a cell that is there looked missing."""
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    cut = project / "data" / "demo_mutant_01.ome.tif"
    # as acquisition software writes it: the metadata first, then the planes (tifffile itself puts the
    # metadata last, so a cut copy of its files is not read at all)
    import tifffile
    with tifffile.TiffFile(cut) as f:
        xml, planes = f.ome_metadata, f.asarray()
    tifffile.imwrite(cut, planes.reshape(-1, *planes.shape[-2:]), description=xml, metadata=None, photometric="minisblack")
    rows = {r["file"]: r for r in inventory(project, log=quiet)}
    assert rows["demo_mutant_01.ome.tif"]["flags"] == "" and rows["demo_mutant_01.ome.tif"]["n_z"] == 16
    data = cut.read_bytes()
    cut.write_bytes(data[: len(data) // 2])
    rows = {r["file"]: r for r in inventory(project, log=quiet)}
    assert "file is incomplete" in rows["demo_mutant_01.ome.tif"]["flags"]
    assert "file is incomplete" not in rows["demo_control_01.ome.tif"]["flags"]
    prepare(project, log=quiet)
    key = read_csv(project / "key" / "key.csv")
    assert [r["file"] for r in key] == ["demo_control_01.ome.tif"]
    (left,) = [r for r in read_csv(project / "outputs" / "excluded.csv") if r.get("local_path")]
    assert left["local_path"].endswith("demo_mutant_01.ome.tif") and "incomplete" in left["reason"]


def test_a_file_open_in_another_program_does_not_stop_the_scan(tmp_path, monkeypatch):
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    real = readers.sha256

    def locked(path, *a, **k):
        if Path(path).name == "demo_mutant_01.ome.tif":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(readers, "sha256", locked)
    rows = inventory(project, log=quiet)
    assert [r["file"] for r in rows] == ["demo_control_01.ome.tif"]
    manifest = {Path(r["relative_path"]).name: r for r in read_csv(project / "outputs" / "manifest.csv")}
    assert manifest["demo_mutant_01.ome.tif"]["role"] == "unreadable file"


def test_a_demo_never_replaces_an_existing_project(tmp_path):
    project = make_demo(tmp_path / "p", n_per_group=1, log=quiet)
    before = (project / "config.json").read_bytes()
    with pytest.raises(SystemExit, match="already holds a wormscore project"):
        make_demo(project, n_per_group=1, log=quiet)
    from wormscore.realdemo import make_real_demo
    with pytest.raises(SystemExit, match="already holds a wormscore project"):
        make_real_demo(project, log=quiet, fetch=lambda *a: pytest.fail("nothing may be downloaded"))
    assert (project / "config.json").read_bytes() == before


def test_pages_have_no_regex_lookbehind():
    """Safari before 16.4 cannot parse one; the whole home page then stayed on its loading screen."""
    for name in ("home.js", "app.js", "form.js", "terms.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert "(?<=" not in text and "(?<!" not in text, name
