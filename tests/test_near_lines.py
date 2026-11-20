"""A preset matched an excitation only at its exact laser lines (1.26.21). A widefield LED a few nm away was
not recognised: idr0011's TagYFP at 508 nm (YFP's lines 514, 515) and mRFP at 590 nm (mCherry's 587). An
excitation within readers.NEAR_NM of a preset line now matches too, together with the emission, only when
no channel matched exactly and exactly one channel fits, and always with a flag. Not tried under
readers.as_before(), so an existing project reads as before until it scans again."""
import pytest

from test_readers import HARDWARE, write_lif
from test_leica_lifetime import stack
from wormscore import checkreading as K
from wormscore import config as C
from wormscore import readers


def cfg(preset):
    c = K.default_settings()
    c["marker"] = C.resolve_marker({"preset": preset})
    return c


def roles_of(preset, ex, em):
    return readers.near_match(["OTHER"] * len(ex), cfg(preset), readers.named_near(ex, em))


def test_the_tolerance_is_ten_nm():
    assert readers.NEAR_NM == 10            # the number METHODS.md gives


def test_an_led_a_few_nm_from_the_line_is_found_with_a_flag():
    assert roles_of("mCherry", ["590", "508"], ["612", "524"]) == (
        ["MARKER", "OTHER"], ["mCherry channel found by its emission and an excitation of 590 nm (3 nm from the "
                              "preset's 587 nm line)"])
    assert roles_of("YFP", ["590", "508"], ["612", "524"])[0] == ["OTHER", "MARKER"]
    assert roles_of("YFP", ["504"], ["524"])[0] == ["MARKER"]            # 10 nm: still near
    assert roles_of("YFP", ["503"], ["524"])[0] == ["OTHER"]             # 11 nm: not


@pytest.mark.parametrize("ex, em", [(["508"], [None]),             # no emission recorded
                                    (["508"], ["600"]),            # an emission outside YFP's range
                                    (["508", "509"], ["524", "530"])])   # two channels fit: not guessed
def test_no_near_match_without_its_emission_or_when_two_fit(ex, em):
    assert roles_of("YFP", ex, em) == (["OTHER"] * len(ex), [])


def test_an_exact_match_is_never_changed():
    c = cfg("YFP")
    roles = ["MARKER", "OTHER"]
    assert readers.near_match(roles, c, readers.named_near(["514", "508"], ["527", "524"])) == (roles, [])


def test_gfp_has_no_emission_range_so_named_files_never_match_near():
    assert roles_of("GFP", ["490"], ["510"]) == (["OTHER"], [])


def test_read_as_before_nothing_changes():
    with readers.as_before():
        assert roles_of("mCherry", ["590"], ["612"]) == (["OTHER"], [])


def test_a_leica_line_near_the_presets_with_its_band(tmp_path):
    """A STELLARIS or SP8 white light laser can be set to any line: 496 nm with a band from 500 nm."""
    path = tmp_path / "wll.lif"
    write_lif(path, [("scan", stack())], hardware=HARDWARE.replace('LaserLine="488"', 'LaserLine="496"'))
    img = readers.leica_images(path, K.default_settings())[0]
    assert img["channel_roles"] == ["MARKER", "BF"]
    assert img["flags"] == ["GFP channel found by its emission and an excitation of 496 nm (8 nm from the preset's "
                            "488 nm line)"]
    with readers.as_before():
        assert readers.leica_images(path, K.default_settings())[0]["channel_roles"] == ["OTHER", "BF"]
    far = tmp_path / "far.lif"
    write_lif(far, [("scan", stack())], hardware=HARDWARE.replace('LaserLine="488"', 'LaserLine="499"'))
    assert readers.leica_images(far, K.default_settings())[0]["channel_roles"] == ["OTHER", "BF"]


def test_only_the_reporter_is_matched_near_and_the_leica_colour_table_counts():
    """Found by review: an identity channel found near its line would be drawn on a coded image that had none
    after a new scan. Only the reporter is matched near (an image without one was never coded). A Leica
    preset that names a colour table needs it for a near match as for an exact one."""
    c = cfg("GFP")
    c["identity"] = C.resolve_marker({"preset": "mCherry"})
    assert readers.near_match(["OTHER", "OTHER"], c, readers.named_near(["508", "590"], [None, "612"])) == (
        ["OTHER", "OTHER"], [])
    at = [{"band": "500-550", "lasers": [("496", 10.0)], "detector": "HyD 1"}]
    m = dict(cfg("GFP")["marker"])
    assert readers.leica_near(at, ["Green"])(0, m) == (496.0, 488.0)
    assert readers.leica_near(at, ["Red"])(0, m) is None                    # GFP's colour table is Green
