"""Nikon .nd2, Olympus .oib/.oif and ImageJ hyperstack TIFF.

The ImageJ tests write their own small files. The Nikon and Olympus tests use public sample files
from the OME sample image collection (CC BY 4.0, downloaded once and checked by SHA-256, never stored
in this repository); they run when WORMSCORE_SAMPLE_TESTS=1 is set.
"""
import copy
import hashlib
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import tifffile

from wormscore import config as C
from wormscore import readers as R
from wormscore.pipeline import inventory, prepare, read_csv

OME = "https://downloads.openmicroscopy.org/images"
SAMPLES = {
    # Zenodo record 17186598 (CC BY 4.0): a 17-plane stack, one fluorescence channel
    "zenodo17186598.nd2": (f"{OME}/ND2/zenodo-17186598/Experiment_0001.nd2",
                      "cdba1aa1625a9a93cd0ee00628400ef049a7034f90cd6a33161e502846e099aa"),
    # image.sc topic 71616 (CC BY 4.0): FluoView FV1000, DAPI and Alexa Fluor 594, 6 planes
    "cord.oib": (f"{OME}/Olympus-FluoView/imagesc-71616/20220824_4492_cord_dapi__iba568_60x.oib",
                 "580d36a9d0dcac1c77ccd6780ad988e94e10cf90d065b9da14fe0e1de83c01af"),
}


def sample(name):
    if os.environ.get("WORMSCORE_SAMPLE_TESTS") != "1":
        pytest.skip("set WORMSCORE_SAMPLE_TESTS=1 to test with the public sample files")
    cache = Path(os.environ.get("WORMSCORE_SAMPLE_DIR", Path.home() / ".cache" / "wormscore-test-samples"))
    cache.mkdir(parents=True, exist_ok=True)
    url, digest = SAMPLES[name]
    path = cache / name
    if not path.exists():
        urllib.request.urlretrieve(url, path)
    if digest:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{name} is not the expected sample file"
    return path


def cfg_with(**marker):
    cfg = copy.deepcopy(C.DEFAULTS)
    cfg["marker"].update(marker)
    return cfg


# ------------------------------------------------------------------ small pieces

def test_new_raw_formats_are_recognised():
    assert R.file_role(Path("a/scan.nd2")) == "raw image"
    assert R.file_role(Path("a/scan.oib")) == "raw image"
    assert R.file_role(Path("a/scan.oif")) == "raw image"
    # the planes of an .oif are read through the .oif file, never on their own
    assert R.file_role(Path("a/scan.oif.files/s_C001Z001.tif")) == R.OIF_PART
    # a plain TIFF stays unused unless its source turns ImageJ reading on
    assert R.file_role(Path("a/stack.tif")) == R.NOT_USED


def test_olympus_numbers_with_a_decimal_comma():
    assert R._olympus_number("0,207.0") == pytest.approx(0.207)
    assert R._olympus_number("1,35.0") == pytest.approx(1.35)
    assert R._olympus_number("39,4.0") == pytest.approx(39.4)
    assert R._olympus_number("60.0") == 60.0
    assert R._olympus_number(510) == 510.0


def test_emission_range_is_used_only_when_set():
    assert R.marker_from_named("Mono", None, None, False, cfg_with(), emission=525) == "OTHER"
    assert R.marker_from_named("Mono", None, None, False, cfg_with(emission_nm=[500, 550]), emission=525) == "MARKER"
    assert R.marker_from_named("Mono", None, None, False, cfg_with(emission_nm=[500, 550]), emission=610) == "OTHER"


def test_config_checks_imagej_and_emission_settings():
    base = copy.deepcopy(C.DEFAULTS)
    for bad in ({"channels": []}, {"channels": ["marker", "red"]}, {"channels": ["other", "brightfield"]},
                {"channels": ["marker", "marker"]}, ["marker"]):
        cfg = copy.deepcopy(base)
        cfg["sources"] = [{"name": "s", "path": ".", "imagej_tiff": bad}]
        with pytest.raises(SystemExit):
            C.check(cfg)
    cfg = copy.deepcopy(base)
    cfg["sources"] = [{"name": "s", "path": ".", "imagej_tiff": {"channels": ["marker", "brightfield"]}}]
    C.check(cfg)
    cfg["marker"]["emission_nm"] = [550, 500]
    with pytest.raises(SystemExit):
        C.check(cfg)


# ------------------------------------------------------------------ ImageJ hyperstack TIFF

def write_imagej(path, n_z=12, n_c=2, pixel_um=0.2, spacing=1.0):
    rng = np.random.default_rng(3)
    data = rng.integers(100, 4000, size=(n_z, n_c, 64, 80), dtype=np.uint16)
    tifffile.imwrite(path, data, imagej=True, resolution=(1 / pixel_um, 1 / pixel_um),
                     metadata={"axes": "ZCYX", "spacing": spacing, "unit": "micron"})
    return data


def imagej_project(tmp_path, imagej_tiff):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    data = write_imagej(data_dir / "worm_mutant_01.tif")
    write_imagej(data_dir / "MAX_worm_mutant_01.tif")         # a projection made later: never raw
    src = {"name": "stacks", "path": str(data_dir), "default_genotype": "mutant"}
    if imagej_tiff is not None:
        src["imagej_tiff"] = imagej_tiff
    project = tmp_path / "project"
    project.mkdir()
    (project / "config.json").write_text(json.dumps({"project_name": "t", "sources": [src]}), encoding="utf-8")
    return project, data


def test_imagej_tiff_is_not_used_unless_the_source_turns_it_on(tmp_path):
    project, _ = imagej_project(tmp_path, None)
    rows = inventory(project, log=lambda *a: None)
    assert rows == []
    roles = {r["relative_path"]: r["role"] for r in read_csv(project / "outputs" / "manifest.csv")}
    assert roles["worm_mutant_01.tif"] == R.NOT_USED
    assert roles["MAX_worm_mutant_01.tif"].startswith("derived")


def test_imagej_tiff_with_a_stated_channel_order(tmp_path):
    project, data = imagej_project(tmp_path, {"channels": ["marker", "brightfield"], "objective": "60x/1.4 oil"})
    rows = inventory(project, log=lambda *a: None)
    assert len(rows) == 1                                      # the MAX_ projection stays out
    r = rows[0]
    assert (r["format"], r["channel_roles"], r["marker_channel"], r["bf_channel"]) == ("ImageJ TIFF", "MARKER/BF", 0, 1)
    assert (r["n_z"], r["pixel_um"], r["z_step_um"], r["objective"]) == (12, 0.2, 1.0, "60x/1.4 oil")
    img = {**r, "_reader": ("ij", r["local_path"], 0, r["reader_axes"])}
    assert np.array_equal(R.read(img, 0), data[:, 0]) and np.array_equal(R.read(img, 1), data[:, 1])
    prepare(project, log=lambda *a: None)
    log = (project / "log.md").read_text(encoding="utf-8")
    assert '"imagej_tiff"' in log and '"brightfield"' in log  # the stated channel order is on record
    assert len(read_csv(project / "key" / "key.csv")) == 1


def test_imagej_tiff_with_the_wrong_channel_count_is_unresolved(tmp_path):
    project, _ = imagej_project(tmp_path, {"channels": ["marker"]})
    r = inventory(project, log=lambda *a: None)[0]
    assert r["channel_roles"] == "UNRESOLVED/UNRESOLVED" and "lists 1 channels, the file has 2" in r["flags"]


# ------------------------------------------------------------------ public sample files

def test_nikon_nd2_sample():
    img, = R.images_in(sample("zenodo17186598.nd2"), cfg_with(emission_nm=[490, 510]))
    assert (img["format"], img["n_z"], img["width"], img["height"], img["bits"]) == ("Nikon ND2", 17, 1750, 1664, 8)
    assert img["objective"] == "Plan Apo 60x Oil DIC H" and img["pixel_um"] == pytest.approx(0.0571, abs=1e-4)
    assert img["channel_roles"] == ["MARKER"] and "exposure 128 ms" in img["settings"]
    stack = R.read(img, 0)
    assert stack.shape == (17, 1664, 1750) and stack.dtype == np.uint8
    assert R.images_in(sample("zenodo17186598.nd2"), cfg_with())[0]["channel_roles"] == ["OTHER"]   # no match: never guessed


def test_olympus_oib_and_oif_samples(tmp_path):
    import oiffile
    oib = sample("cord.oib")
    cfg = cfg_with(dye_names=["Alexa Fluor 594"])
    img, = R.images_in(oib, cfg)
    assert (img["system"], img["objective"]) == ("Olympus FLUOVIEW FV1000", "60x/1.35 (UPLSAPO 60X O NA:1.35)")
    assert (img["n_z"], img["pixel_um"], img["z_step_um"], img["bits"]) == (6, 0.207, 0.51, 12)
    assert img["channel_roles"] == ["OTHER", "MARKER"] and img["settings"].startswith("PMT 661 V")
    stack = R.read(img, 1)
    assert stack.shape == (6, 1024, 1024)
    # the same image saved as .oif (a file plus a folder of planes) reads the same
    oiffile.oib2oif(str(oib), location=str(tmp_path))
    oif = next(tmp_path.glob("*.oif"))
    img2, = R.images_in(oif, cfg)
    assert {k: v for k, v in img2.items() if k != "_reader"} == {k: v for k, v in img.items() if k != "_reader"}
    assert np.array_equal(R.read(img2, 1), stack)
