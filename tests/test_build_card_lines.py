"""The Build card's notice of what the last scan reads differently (1.26.21). It showed the first line and
"(and N more)", and always ended "Rebuild, keeping the current codes, to use it", also when a rebuild
does not apply the change (the slide-image lines stay after a rebuild: test_utc_dates, test_zeiss_zoom,
test_leica_dates) or applies it wrongly (a scan in another time zone). Now every line is shown, each with
its own next step, from the kind scan_changes gives it where the line is made. Found by review: a rebuild
does apply a day written day first (with the nearest day's slide images), and would apply another time
zone's day."""
import datetime as dt
import re

from test_flatfield_dates import LINE as DAY_LINE, QUIET, SCORE, by_name, dated, existing_project  # noqa: F401
from test_leica_dates import LINE as LEICA_LINE
from test_utc_dates import LINE as UTC_LINE, ZONE_LINE, before, est, existing as utc_project  # noqa: F401
from test_zeiss_zoom import LINE as ZOOM_LINE, existing as zeiss_project, zeiss  # noqa: F401
from wormscore import home, pipeline, readers, rescan, server
from wormscore.files import read_csv

STEPS = {
    "rebuild": "Rebuild, keeping the current codes, to use it.",
    "slides": "Choose the slide images again to use it (Flat-field correction, Sessions..., Start again).",
    "day": "Choose the slide images again to use it (Flat-field correction, Sessions..., Start again). "
           "A rebuild alone uses it with the nearest day's slide images.",
    "zone": "Scan again on the computer that scanned before, or choose the slide images again. Do not rebuild before that.",
}
GROUP_LINE = "2 coded images move from settings group A to B"


def sentence(line, kind):
    what = re.split(r"; (?:before scoring begins,|choose the slide images again|scan there,)", line)[0]
    return f"{what[0].upper()}{what[1:]}. {STEPS[kind]}"


def card(project, pairs, monkeypatch, locked=False):
    monkeypatch.setattr("wormscore.flatfield.lock_reason", lambda p: "Scoring has started." if locked else "")
    return rescan.scan_change_lines(project, {"lines": [x for x, _ in pairs], "kinds": [k for _, k in pairs]})


def test_every_line_is_shown_with_its_own_step(tmp_path, monkeypatch):
    pairs = [(GROUP_LINE, "rebuild"), (LEICA_LINE, "slides"), (ZOOM_LINE, "slides"), (UTC_LINE, "slides"),
             (ZONE_LINE, "zone"), (DAY_LINE, "day")]
    out = card(tmp_path, pairs, monkeypatch)
    assert out == [sentence(x, k) for x, k in pairs]
    assert out[0] == "2 coded images move from settings group A to B. Rebuild, keeping the current codes, to use it."
    assert out[2] == ("The flat-field zoom of 4 coded images is now the scan zoom. Choose the slide images again to "
                      "use it (Flat-field correction, Sessions..., Start again).")
    for text, (_, kind) in zip(out, pairs):
        assert ";" not in text and "(and" not in text
        assert ("Rebuild, keeping" in text) == (kind == "rebuild")


def test_once_scoring_has_begun_the_flat_field_lines_are_left_out(tmp_path, monkeypatch):
    """Coded images then keep the correction they were scored with, and choosing the slide images again is
    not offered: the line would ask for nothing and never go."""
    pairs = [(GROUP_LINE, "rebuild"), (ZOOM_LINE, "slides"), (ZONE_LINE, "zone"), (DAY_LINE, "day")]
    assert card(tmp_path, pairs, monkeypatch, locked=True) == [sentence(GROUP_LINE, "rebuild")]


def test_the_zoom_line_on_a_real_project(tmp_path, zeiss):
    p = zeiss_project(tmp_path)
    zeiss["new"] = True
    pipeline.inventory(p, **QUIET)
    ch = rescan.scan_changes(p)
    assert dict(zip(ch["lines"], ch["kinds"]))[ZOOM_LINE] == "slides"
    assert home.project_status(p)["scan_changes"] == [sentence(ZOOM_LINE, "slides")]
    pr = server.Project(p)
    for r in read_csv(p / "key" / "key.csv"):
        pr.save_score(r["code"], dict(SCORE))
    assert ZOOM_LINE in rescan.scan_changes(p)["lines"] and home.project_status(p)["scan_changes"] == []


def test_the_day_first_line_is_one_a_rebuild_applies(tmp_path, dated):
    p = existing_project(tmp_path)
    pipeline.inventory(p, **QUIET)
    ch = rescan.scan_changes(p)
    assert dict(zip(ch["lines"], ch["kinds"]))[DAY_LINE] == "day"
    assert sentence(DAY_LINE, "day") in home.project_status(p)["scan_changes"]
    pipeline.prepare(p, **QUIET)                      # as the card says: the rebuild uses the nearest day's
    assert DAY_LINE not in rescan.scan_changes(p)["lines"]
    assert any(r["flat_field"].startswith("nearest day") for r in by_name(p).values())


def test_the_time_zone_line_says_not_to_rebuild(tmp_path, before, monkeypatch):
    before["new"] = True
    p = utc_project(tmp_path)
    monkeypatch.setattr(readers, "LOCAL_ZONE", dt.timezone.utc)
    pipeline.inventory(p, **QUIET)
    ch = rescan.scan_changes(p)
    assert list(zip(ch["lines"], ch["kinds"])) == [(ZONE_LINE, "zone")]
    assert home.project_status(p)["scan_changes"] == [sentence(ZONE_LINE, "zone")]


def test_the_card_shows_one_line_as_a_sentence_and_more_as_a_list():
    from pathlib import Path
    js = (Path(home.__file__).parent / "static" / "home.js").read_text(encoding="utf-8")
    body = js[js.index("function scanNote"):js.index("function icon")]
    assert "lines.length === 1" in body and 'h("ul"' in body
