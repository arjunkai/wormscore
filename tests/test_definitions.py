"""A definition and a reference image for each call and answer (1.9.0): stored as wording, shown on the
scoring page, written to log.md with each image's checksum, carried into second-scorer packages."""
import base64
import hashlib
import json
import shutil
import subprocess
import threading

import pytest

from wormscore import config as C
from wormscore import editconfig as E
from wormscore import methods
from wormscore import scoreform as S
from wormscore import scorers
from wormscore.server import make_server
from test_app import request
from test_home import call, desk  # noqa: F401  (desk is a fixture)
from test_scoreform import STATIC, body_with_form, save_after_scoring, score_all

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64          # only the signature is checked
DATA = "data:image/png;base64," + base64.b64encode(PNG).decode()


def with_definitions(project):
    """The project's form with a definition on "dim", an image on "missing" and a scale question."""
    m = E.current(project)["form"]["model"]
    path = S.save_image(project, DATA, "missing.png")
    for c in m["calls"]:
        if c["text"] == "dim":
            c["def"] = "Visible but  weaker than controls."
        if c["text"] == "missing":
            c["image"] = path
    m["questions"].append({"id": None, "label": "Dendrite", "type": "scale", "ask": "neuron",
                           "options": ["full", "short"], "cant_tell": "", "required": False, "skip_when": [],
                           "outcome": "", "defs": {"short": {"text": "Ends before halfway.", "image": ""},
                                                  "full": {"text": "", "image": ""},
                                                  "gone": {"text": "not an answer", "image": ""}}})
    return m, path


def test_definitions_are_stored_as_wording_and_round_trip(fresh_project):
    m, path = with_definitions(fresh_project)
    raw = S.apply(json.loads((fresh_project / "config.json").read_text(encoding="utf-8")), m)
    sc = raw["scoring"]
    assert sc["call_definitions"] == {"dim": {"text": "Visible but weaker than controls."},
                                      "missing": {"image": path}}
    # empty definitions and answers that are not in the question are not kept
    assert sc["neuron_questions"][-1]["definitions"] == {"short": {"text": "Ends before halfway."}}
    cfg = S._cfg_from_raw(raw)
    C.check(cfg)
    assert S.clean(S.to_model_cfg(cfg)) == S.clean(m)
    # wording only: the recorded form (and a package's form hash) does not change with them
    plain = S._cfg_from_raw(dict(raw, scoring={k: v for k, v in sc.items() if k != "call_definitions"}))
    assert S.form_record(cfg) == S.form_record(plain) and scorers.form_hash(cfg) == scorers.form_hash(plain)
    # removing every definition removes the key again
    for c in m["calls"]:
        c["def"], c["image"] = "", ""
    assert "call_definitions" not in S.apply(raw, m)["scoring"]


def test_reference_images_are_checked_and_stored_by_checksum(fresh_project):
    path = S.save_image(fresh_project, DATA, "a.png")
    assert path == f"criteria/{hashlib.sha256(PNG).hexdigest()[:16]}.png"
    assert (fresh_project / path).read_bytes() == PNG
    assert S.save_image(fresh_project, base64.b64encode(PNG).decode()) == path     # the same picture is stored once
    jpg = S.save_image(fresh_project, base64.b64encode(b"\xff\xd8\xff\xe0" + b"1" * 20).decode())
    assert jpg.endswith(".jpg")
    with pytest.raises(ValueError, match="not a PNG or JPEG"):
        S.save_image(fresh_project, base64.b64encode(b"II*\x00 a tiff").decode(), "cell.tif")
    with pytest.raises(ValueError, match="could not be read"):
        S.save_image(fresh_project, "not base64 at all!")
    with pytest.raises(ValueError, match="larger than"):
        S.save_image(fresh_project, "A" * (S.MAX_IMAGE_BYTES * 2))
    m = E.current(fresh_project)["form"]["model"]
    m["calls"][0]["image"] = "../config.json"
    with pytest.raises(ValueError, match="reference image"):
        S.clean(m)
    m["calls"][0]["image"], m["calls"][0]["def"] = "", "x" * (S.MAX_DEFINITION + 1)
    with pytest.raises(ValueError, match="longer than"):
        S.clean(m)
    assert S.image_file(fresh_project, "criteria/../../x.png") is None


def test_saving_logs_the_definitions_with_each_image_checksum(fresh_project):
    m, path = with_definitions(fresh_project)
    r = E.save(fresh_project, body_with_form(fresh_project, m))
    assert r["form_changed"]
    line = next(x for x in (fresh_project / "log.md").read_text(encoding="utf-8").splitlines()
                if x.startswith("- " + S.DEFS_LOG))
    logged = json.loads(line[len("- " + S.DEFS_LOG):])
    assert logged["call"]["missing"] == {"image": path, "sha256": hashlib.sha256(PNG).hexdigest()}
    assert logged["call"]["dim"] == {"text": "Visible but weaker than controls."}
    assert list(logged)[0] == "call" and len(logged) == 2      # the calls, then the scale question
    # Methods says what the scorer was shown
    text = methods.definitions_sentence(C.load(fresh_project))
    assert "a written definition for 2 answer categories and a reference image for 1 answer category" in text
    assert methods.definitions_sentence(C.merge(C.DEFAULTS, {})) == ""


def test_a_definition_added_after_the_lock_is_wording_and_keeps_the_tests(fresh_project, monkeypatch):
    from wormscore.home import project_status
    score_all(fresh_project)
    m = E.current(fresh_project)["form"]["model"]
    m["calls"][1]["def"] = "Weaker than controls."
    with pytest.raises(ValueError, match="locked"):
        E.save(fresh_project, body_with_form(fresh_project, m))
    r = save_after_scoring(fresh_project, dict(body_with_form(fresh_project, m), unlock_reason="add a definition"), monkeypatch)
    log = (fresh_project / "log.md").read_text(encoding="utf-8")
    assert r["after_lock"] and "Only wording changed" in log
    assert S.DEFS_LOG + json.dumps({"call": {"dim": {"text": "Weaker than controls."}}}) in log
    assert project_status(fresh_project)["form"]["changed_after_lock"] is False


def test_presets_put_definitions_with_their_answers():
    fate = S.preset_model("fate")
    assert [c["text"] for c in fate["calls"]] == ["on", "dim", "off"] and "wild type" in fate["calls"][1]["def"]
    assert not any(r.startswith(("On:", "Dim:", "Off:")) for r in fate["rules"])
    dendrite = S.preset_model("dendrite")
    assert set(dendrite["questions"][0]["defs"]) == {"full length", "short"}
    # each preset with a source names it, in the app and in its rules
    for name, p in S.PRESETS.items():
        if p["source"]:
            assert p["source"].split(",")[0] in " ".join(S.preset_model(name)["rules"]), name
    assert S.WARN_LEVELS == 5
    assert not any(r.startswith("Seen:") for r in dendrite["rules"])


def test_the_scoring_server_serves_only_reference_images(fresh_project):
    m, path = with_definitions(fresh_project)
    E.save(fresh_project, body_with_form(fresh_project, m))
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert request(port, "GET", "/" + path) == (200, PNG)
        assert request(port, "GET", "/criteria/0000000000000000.png")[0] == 404
        assert request(port, "GET", "/criteria/..%2Fconfig.json")[0] == 404
        st, data = request(port, "GET", "/api/config")
        assert json.loads(data)["scoring"]["call_definitions"]["missing"] == {"image": path}
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_home_page_adds_and_shows_reference_images(desk):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    st, r = call(port, "POST", "/api/project-settings", {"action": "criteria-image", "data": DATA, "name": "x.png"})
    assert st == 200 and (project / r["path"]).read_bytes() == PNG
    st, r2 = call(port, "POST", "/api/project-settings", {"action": "criteria-image", "data": "bm90IGFuIGltYWdl"})
    assert st == 400 and "not a PNG or JPEG" in r2["error"]
    assert call(port, "GET", "/" + r["path"])[0] == 200
    assert call(port, "GET", "/criteria/config.json")[0] == 404


def test_a_package_carries_the_reference_images(fresh_project, tmp_path):
    m, path = with_definitions(fresh_project)
    E.save(fresh_project, body_with_form(fresh_project, m))
    pkg = scorers.export_package(fresh_project, "Sam", tmp_path / "pkg", fraction=0.5, log=lambda *a: None)
    assert (pkg / path).read_bytes() == PNG
    assert json.loads((pkg / "config.json").read_text(encoding="utf-8"))["scoring"]["call_definitions"]["missing"] == {"image": path}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_form_js_lists_only_answers_with_a_definition():
    # a tiny stand-in for the page's h() and localStorage, enough to read what meanings() builds
    sc = {"calls": ["normal", "dim", "missing"], "call_definitions": {"dim": {"text": "weaker"}, "missing": {"image": "criteria/a.png"}},
          "neuron_questions": [{"id": "d", "label": "Dendrite", "options": ["full", "short"], "definitions": {"short": {"text": "ends early"}}}],
          "animal_questions": []}
    js = """
const h = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat().filter(k => k != null) });
const text = n => typeof n === "string" ? n : (n.kids || []).map(text).join("|");
""" + (STATIC / "form.js").read_text(encoding="utf-8") + f"""
const out = wsForm.meanings(h, {json.dumps(sc)});
const none = wsForm.meanings(h, {{ calls: ["a", "b"], neuron_questions: [] }});
console.log(JSON.stringify([out.length, out[0].attrs.open, text(out[0]), none.length,
  wsForm.radios(h, "n1_call", ["dim"], {{ dim: {{ text: "weaker" }} }})[0].attrs.title]));"""
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out[0] == 1 and out[1] is True and out[3] == 0 and out[4] == "weaker"
    assert out[2] == "What each answer means|Call|dim|weaker|missing|||Dendrite|short|ends early"
