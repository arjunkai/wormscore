"""Gut autofluorescence check after unblinding (1.22.0)."""
import numpy as np
import pytest
import tifffile

from wormscore import autofluor as AF
from wormscore import methods  # noqa: F401
from wormscore.demo import PX_UM, Z_UM, make_demo
from wormscore.pipeline import inventory, prepare, read_csv
from wormscore.unblind import unblind

from test_results_options import write_scores

QUIET = {"log": lambda *a: None}


def with_blue(project, bright):
    """Rewrite the demo's images with a third, unlabelled DAPI channel: flat noise, plus the GFP signal
    itself in the images named in bright (as if the 'cell' were a gut granule glowing in every channel)."""
    rng = np.random.default_rng(1)
    for f in sorted((project / "data").glob("*.ome.tif")):
        gfp, bf = tifffile.imread(f).astype(np.float64)
        blue = 200 + rng.normal(0, 8, gfp.shape)
        if f.name in bright:
            blue += gfp - np.median(gfp)
        tifffile.imwrite(f, np.clip(np.stack([gfp, bf, blue]), 0, 65535).astype(np.uint16), photometric="minisblack",
                         ome=True, metadata={"axes": "CZYX", "PhysicalSizeX": PX_UM, "PhysicalSizeY": PX_UM,
                                             "PhysicalSizeZ": Z_UM, "Channel": {"Name": ["GFP", "Brightfield", "DAPI"]}})


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    p = tmp_path_factory.mktemp("af") / "p"
    make_demo(p, n_per_group=2, **QUIET)
    with_blue(p, {"demo_control_01.ome.tif"})
    inventory(p, **QUIET)
    prepare(p, **QUIET)
    rows = [{"code": r["code"], "usable": "yes", "count_this_image": "yes", "n1_call": "normal", "n2_call": "normal"}
            for r in read_csv(p / "key" / "key.csv")]
    write_scores(p, rows)
    return p


def test_it_runs_only_after_unblinding(project):
    assert AF.available(project) == "DAPI"
    with pytest.raises(SystemExit, match="after Unblind"):
        AF.check(project, **QUIET)


def test_a_cell_bright_in_the_unlabelled_channel_is_flagged(project):
    unblind(project, finished=True, anyway=True, **QUIET)
    s = AF.check(project, **QUIET)
    rows = read_csv(project / "outputs" / "unblinded" / AF.RESULTS)
    flagged = {r["code"] for r in rows if r["possibly_gut_autofluorescence"] == "True"}
    key = {r["code"]: r for r in read_csv(project / "key" / "key.csv")}
    assert flagged and all(key[c]["file"].startswith("demo_control_01") for c in flagged)
    assert s["channel"] == "DAPI" and s["flagged"] == len([r for r in rows if r["possibly_gut_autofluorescence"] == "True"])
    assert AF.LOG_TITLE in (project / "log.md").read_text(encoding="utf-8")
    text = (project / "outputs" / "unblinded" / "methods_text.md").read_text(encoding="utf-8")
    assert text.count(AF.METHODS_HEAD) == 1
    AF.check(project, **QUIET)                       # run again: the paragraph is replaced, not added twice
    text = (project / "outputs" / "unblinded" / "methods_text.md").read_text(encoding="utf-8")
    assert text.count(AF.METHODS_HEAD) == 1
    assert text.index(AF.METHODS_HEAD) < text.index("## Images per genotype")
    unblind(project, finished=True, anyway=True, **QUIET)       # Unblind again writes it in the same place
    again = (project / "outputs" / "unblinded" / "methods_text.md").read_text(encoding="utf-8")
    assert again.count(AF.METHODS_HEAD) == 1
    para = lambda t: t.split(AF.METHODS_HEAD)[1].split("\n\n")[0]
    assert para(again) == para(text)


def test_the_home_page_offers_it_after_unblinding(project):
    from wormscore.home import project_status
    af = project_status(project)["autofluor"]
    assert af["channel"] == "DAPI" and af["result"]["regions"] >= 1


def test_images_without_an_unlabelled_channel_are_not_offered(tmp_path):
    p = tmp_path / "plain"
    make_demo(p, n_per_group=1, **QUIET)
    inventory(p, **QUIET)
    assert AF.available(p) is None
    assert AF.methods_sentence(p) == ""


def test_browse_shows_the_flag_after_unblinding(project):
    from wormscore import browse as B
    b = B.Browse(project)
    rows = read_csv(project / "outputs" / "unblinded" / AF.RESULTS)
    flagged = {r["code"] for r in rows if r["possibly_gut_autofluorescence"] == "True"}
    checked = {r["code"] for r in rows}
    worms = {w["code"]: w for w in (b.summary(it) for it in b.items.values())}
    assert {c for c, w in worms.items() if w.get("autofluor")} == flagged
    info = lambda c: {x["k"]: x["v"] for x in b.info(b.items[c])}
    c = sorted(flagged)[0]
    assert info(c)["Gut autofluorescence"].startswith("Possibly, in ") and "(DAPI channel)" in info(c)["Gut autofluorescence"]
    calm = sorted(checked - flagged)
    if calm:
        assert info(calm[0])["Gut autofluorescence"] == "Not flagged (DAPI channel)"
    unchecked = sorted(set(worms) - checked)
    if unchecked:
        assert "Gut autofluorescence" not in info(unchecked[0])
