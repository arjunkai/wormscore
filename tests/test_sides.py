"""Questions asked for each side, left and right (1.14.0), a form with no count per image, and the
Counts and position template. Forms made before 1.14.0 must record, hash and analyse as before."""
import json

import pytest

from wormscore import config as C
from wormscore import criteriafile as F
from wormscore import methods, practice, ranks, scorers, stats
from wormscore import scoreform as S
from wormscore.pipeline import prepare, read_csv
from wormscore.server import score_fields
from wormscore.unblind import unblind

from test_results_options import edit_config, write_scores

COUNT_SIDE = {"id": "cells", "label": "Cells", "type": "count", "options": ["0", "1", "2", "3+", "can't tell"],
              "cant_tell": "can't tell"}
YESNO_SIDE = {"id": "ectopic", "label": "Ectopic cell?", "options": ["yes", "no", "can't tell"]}


def cfg_with(side=(COUNT_SIDE, YESNO_SIDE), ro=None, oc=None):
    return C.merge(C.DEFAULTS, {"scoring": {"side_questions": [dict(q) for q in side]},
                                "unblinding": {"rank_outcomes": ro or [], "question_outcomes": oc or []}})


def row(genotype="wt", left="", right="", ect=("", ""), system="A"):
    return {"code": "x", "system": system, "genotype": genotype, "score_left_cells": left, "score_right_cells": right,
            "score_left_ectopic": ect[0], "score_right_ectopic": ect[1]}


# ------------------------------------------------------------------ nothing changes for earlier forms

def test_forms_without_side_questions_record_and_hash_as_before():
    cfg = C.merge(C.DEFAULTS, {})
    assert "side_questions" not in S.form_record(cfg)
    assert scorers.form_hash(cfg) == "253567883375e6f0"
    assert not any(f.startswith(("left_", "right_")) for f in score_fields(cfg))
    for name in ("fate", "axon", "degeneration", "dendrite", "blank"):
        m = S.clean(S.preset_model(name))
        assert m["count"]["options"] and all(q["ask"] != "side" for q in m["questions"])


def test_an_empty_side_list_written_by_hand_changes_nothing():
    plain = C.merge(C.DEFAULTS, {})
    empty = C.merge(C.DEFAULTS, {"scoring": {"side_questions": []}})
    assert S.form_record(empty) == S.form_record(plain) and scorers.form_hash(empty) == scorers.form_hash(plain)


# ------------------------------------------------------------------ fields, answers and the record

def test_side_questions_are_saved_left_then_right():
    cfg = cfg_with()
    f = score_fields(cfg)
    assert f[f.index("left_cells"):f.index("left_cells") + 4] == ["left_cells", "right_cells", "left_ectopic", "right_ectopic"]
    allowed = scorers.form_answers(cfg)
    assert allowed["right_cells"] == {"0", "1", "2", "3+", "can't tell", ""}
    rec = S.form_record(cfg)["side_questions"]
    assert [q["id"] for q in rec] == ["cells", "ectopic"] and rec[0]["type"] == "count"
    C.check(cfg)


def test_config_check_refuses_mixed_or_skipped_side_questions():
    cfg = cfg_with(oc=[{"name": "x", "questions": ["ectopic"], "defect_answers": ["yes"]}])
    cfg["scoring"]["neuron_questions"] = [{"id": "n", "label": "N", "options": ["yes", "no"]}]
    C.check(cfg)
    cfg["unblinding"]["question_outcomes"][0]["questions"] = ["ectopic", "n"]
    with pytest.raises(SystemExit, match="not a mix"):
        C.check(cfg)
    bad = cfg_with(side=[dict(YESNO_SIDE, skip_when_calls=["missing"])])
    with pytest.raises(SystemExit, match="only applies to neuron questions"):
        C.check(bad)
    twice = cfg_with()
    twice["scoring"]["neuron_questions"] = [{"id": "cells", "label": "N", "options": ["yes", "no"]}]
    with pytest.raises(SystemExit, match="used twice"):
        C.check(twice)


# ------------------------------------------------------------------ analysis

def test_a_side_count_is_totalled_over_both_sides():
    ro = {"name": "cells", "question": "cells"}
    cfg = cfg_with(ro=[ro])
    assert ranks.animal_value(row(left="1", right="3+"), cfg, ro) == (4, "")
    assert ranks.animal_value(row(left="1", right="can't tell"), cfg, ro) == (None, "unclear")
    assert ranks.animal_value(row(left="1"), cfg, ro) == (None, "incomplete")
    mean = dict(ro, rollup="mean_per_neuron")
    assert ranks.animal_value(row(left="1", right="can't tell"), cfg, mean) == (1, "")
    table, _ = ranks.outcome_table([row(left="1", right="2"), row(left="0", right="0")], cfg, ro)
    assert table[0]["asked"] == "for each side" and table[0]["mean"] == 1.5


def test_a_side_scale_takes_the_worse_side():
    q = {"id": "cells", "label": "Shape", "type": "scale", "options": ["normal", "mild", "severe", "can't tell"],
         "cant_tell": "can't tell"}
    ro = {"name": "shape", "question": "cells"}
    cfg = cfg_with(side=[q], ro=[ro])
    C.check(cfg)
    assert ranks.animal_value(row(left="mild", right="normal"), cfg, ro) == (1, "")
    # the other side cannot change an animal that already has the worst level
    assert ranks.animal_value(row(left="severe", right="can't tell"), cfg, ro) == (2, "")


def test_a_yes_no_side_outcome_counts_the_animal_once():
    oc = {"name": "ectopic cells", "questions": ["ectopic"], "defect_answers": ["yes"], "unclear_answers": ["can't tell"],
          "not_visible_calls": []}
    cfg = cfg_with(oc=[oc])
    C.check(cfg)
    assert stats.outcome_scope(cfg, oc) == "side" and not stats.is_animal_outcome(cfg, oc)
    assert stats.outcome_state(row(ect=("yes", "no")), cfg, oc) == "defect"
    assert stats.outcome_state(row(ect=("no", "can't tell")), cfg, oc) == "normal"
    assert stats.outcome_state(row(ect=("can't tell", "can't tell")), cfg, oc) == "unclear"
    assert stats.outcome_state(row(ect=("no", "")), cfg, oc) == "incomplete"
    assert stats.outcome_state(row(ect=("no", "can't tell")), cfg, dict(oc, unclear="exclude_animal")) == "unclear"
    text = " ".join(methods.outcome_sentences(cfg, []))
    assert "left and the right side" in text and "either assessed side" in text


def test_hidden_repeats_compare_side_outcomes():
    oc = {"name": "ectopic cells", "questions": ["ectopic"], "defect_answers": ["yes"], "not_visible_calls": []}
    cfg = cfg_with(oc=[oc])
    a = row(ect=("yes", "no")) | {"score_usable": "yes"}
    b = row(ect=("no", "no")) | {"score_usable": "yes"}
    rel = {r["measure"]: r for r in stats.reliability([(a, b)], cfg)}
    assert rel["ectopic cells"]["same_answer"] == 0


# ------------------------------------------------------------------ no count per image

def test_a_form_can_ask_no_count_per_image():
    cfg = C.merge(C.DEFAULTS, {"scoring": {"count_question": {"label": "x", "options": []}}})
    assert "cells_seen" in score_fields(cfg)            # the column stays, empty, so the layout is unchanged
    rel = {r["measure"] for r in stats.reliability([], cfg)}
    assert "cells seen" not in rel
    assert "cells_seen" not in practice.key_fields(cfg)
    assert "cells_seen" in practice.key_fields(C.merge(C.DEFAULTS, {}))
    m = S.to_model_cfg(cfg)
    assert S.clean(m)["count"]["options"] == []
    with pytest.raises(ValueError, match="at least 2"):
        S.clean(dict(m, count={"label": "x", "options": ["0"]}))


# ------------------------------------------------------------------ builder and template

def test_builder_round_trips_side_questions():
    m = S.preset_model("blank")
    m["questions"] = [{"label": "Cells here", "type": "count", "ask": "side", "count": {"min": 0, "max": 4, "top": True},
                       "cant_tell": "", "required": True, "skip_when": ["not seen"], "outcome": "cells", "rollup": "",
                       "defs": {}}]
    raw = S.apply(C.template("x"), m)
    sq = raw["scoring"]["side_questions"]
    assert sq[0]["options"] == ["0", "1", "2", "3", "4+"] and "skip_when_calls" not in sq[0]
    assert raw["unblinding"]["rank_outcomes"] == [{"name": "cells", "question": "cells_here", "rollup": "sum"}]
    cfg = S._cfg_from_raw(raw)
    C.check(cfg)
    assert S.unsupported(cfg) == ""
    back = S.to_model_cfg(cfg)["questions"][0]
    assert back["ask"] == "side" and back["skip_when"] == []
    bad = dict(m["questions"][0], ask="sideways")
    with pytest.raises(ValueError, match="for each side"):
        S.clean(dict(m, questions=[bad]))


def test_an_outcome_cannot_mix_side_and_neuron_questions():
    m = S.preset_model("axon")
    m["questions"][1]["ask"] = "side"
    with pytest.raises(ValueError, match="not a mix"):
        S.clean(m)


def test_counts_template():
    m = S.clean(S.preset_model("counts"))
    assert m["count"]["options"] == []
    asks = {q["id"]: q["ask"] for q in m["questions"]}
    assert asks == {"position": "neuron", "cells": "side"} and m["primary"] == "cell count"
    raw = S.apply(C.template("x"), S.preset_model("counts"))
    cfg = S._cfg_from_raw(raw)
    assert {"left_cells", "right_cells", "n1_position"} <= set(score_fields(cfg))
    # a criteria file of it imports again unchanged
    name, text = F.export(None, S.preset_model("counts"), "Counts")
    assert json.loads(text)["criteria"]["questions"][1]["ask"] == "side"


def test_unblind_tests_a_side_count(fresh_project):
    raw = json.loads((fresh_project / "config.json").read_text())
    new = S.apply(raw, S.preset_model("counts"))
    (fresh_project / "config.json").write_text(json.dumps(new))
    prepare(fresh_project, log=lambda *a: None)
    rows = []
    for r in read_csv(fresh_project / "key" / "key.csv"):
        mut = r["genotype"] == "mutant"
        rows.append({"code": r["code"], "usable": "yes", "count_this_image": "yes", "n1_call": "seen", "n2_call": "not seen",
                     "n1_position": "anterior" if mut else "in place",
                     "left_cells": "2" if mut else "1", "right_cells": "3+" if mut else "1"})
    write_scores(fresh_project, rows)
    out = unblind(fresh_project, finished=True, anyway=True, log=lambda *a: None)
    table = {r["genotype"]: r for r in read_csv(out / "rank_outcomes.csv")}
    assert table["mutant"]["median"] == "5" and table["control"]["median"] == "2"
    assert table["mutant"]["asked"] == "for each side"
    per_neuron = read_csv(out / "per_neuron.csv")
    assert {"cell count (left)", "cell count (right)", "cell count (animal value)", "position defect"} <= set(per_neuron[0])
    pos = {r["genotype"]: r for r in read_csv(out / "question_outcomes.csv") if r["outcome"] == "position defect"}
    assert pos["mutant"]["penetrance_pct"] == "100.0"
    text = (out / "methods_text.md").read_text(encoding="utf-8")
    assert "left and the right side of each animal" in text and "total over both sides" in text
