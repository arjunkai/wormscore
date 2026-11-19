"""Two scoring windows that save one image's score one after the other (as 2a98f93 did for outlines).
The later save still wins; the window that saves learns that it replaced a score saved elsewhere since it
loaded the image, and the other window can ask which score is saved now."""
import datetime as real_dt
import json
import shutil

from wormscore import server

from test_save_outlines import get, post, running

A = {"usable": "no", "unusable_reason": "out of focus", "flag_answers": {}}
B = {"usable": "no", "unusable_reason": "damaged", "notes": "second window", "flag_answers": {}}
STORED = ("scores.json", "scores.csv", "score_history.csv")


def test_a_score_save_says_when_it_replaced_a_score_another_window_saved_meanwhile(fresh_project):
    srv, port = running(fresh_project)
    save = lambda body, loaded: json.loads(post(port, "/api/worm/W0001/score", {**body, "loaded": loaded})[1])
    try:
        opened = get(port, "/api/worm/W0001")[1]["score_v"]             # both windows open the image: no score yet
        assert get(port, "/api/worm/W0001/score-version")[1] == {"version": opened}
        a = save(A, opened)                                               # window A
        assert set(a) == {"ok", "saved", "version"} and a["version"] != opened
        assert get(port, "/api/worm/W0001/score-version")[1] == {"version": a["version"]}   # A looks again: its own
        b = save(B, opened)                                               # window B, which never saw A's score
        assert b["replaced"] is True and b["version"] != a["version"]
        stored = json.loads((fresh_project / "scoring" / "scores.json").read_text(encoding="utf-8"))["W0001"]
        assert stored["notes"] == "second window" and "loaded" not in stored
        assert get(port, "/api/worm/W0001/score-version")[1]["version"] == b["version"]  # A looks again: replaced
        assert get(port, "/api/worm/W0001")[1]["score_v"] == b["version"]
        again = save(A, a["version"])                                     # A saves anyway: it wins, and is told
        assert again["replaced"] is True
        assert "replaced" not in save(B, again["version"])               # a window that saw the saved score
        assert get(port, "/api/worm/W0002/score-version")[1] == {"version": opened}       # another image: none saved
        assert get(port, "/api/worm/W9999/score-version")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


class FixedTime(real_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 19, 10, 0, 0)


def test_what_is_stored_is_the_same_with_and_without_the_version(fresh_project, tmp_path, monkeypatch):
    """One scoring window: the same scores.json, scores.csv and score_history.csv byte for byte, and a page
    that sends no version (an earlier one left open) gets the answer it always got. Drafts as before."""
    monkeypatch.setattr(server.dt, "datetime", FixedTime)
    other = tmp_path / "other"
    shutil.copytree(fresh_project, other)
    stored = []
    for project, with_version in ((fresh_project, True), (other, False)):
        srv, port = running(project)
        try:
            for code, body in (("W0001", A), ("W0002", B), ("W0001", B)):
                assert post(port, f"/api/worm/{code}/draft", {"values": {"notes": "half done"}})[0] == 200
                loaded = get(port, f"/api/worm/{code}")[1]
                assert loaded["draft"]["values"] == {"notes": "half done"}
                answer = json.loads(post(port, f"/api/worm/{code}/score",
                                         {**body, **({"loaded": loaded["score_v"]} if with_version else {})})[1])
                assert "replaced" not in answer
                if not with_version:
                    assert answer == {"ok": True, "saved": "2026-11-19 10:00:00"}
                assert get(port, f"/api/worm/{code}")[1]["draft"] is None      # a saved score drops the draft, as before
            assert post(port, "/api/worm/W0003/draft", {"values": {"notes": "kept"}})[0] == 200
        finally:
            srv.shutdown()
            srv.server_close()
        stored.append({n: (project / "scoring" / n).read_bytes() for n in STORED})
        assert json.loads((project / "scoring_drafts" / "drafts.json").read_text(encoding="utf-8"))["W0003"]["values"] == {"notes": "kept"}
    assert stored[0] == stored[1]


def test_the_version_sent_with_an_image_names_the_score_sent_with_it(fresh_project):
    """Found by review: the score and its version were read from scores.json one after the other, so a save
    from another window landing between the two reads paired the old score with the new version, and the
    window that showed the old score was never warned."""
    srv, port = running(fresh_project)
    pr, real, landed = srv.project, srv.project.load, []

    def load(name, default):
        got = real(name, default)
        if name == "scores.json" and not landed:        # another window saves right after the first read
            landed.append(1)
            pr.save_score("W0001", dict(B))
        return got
    pr.load = load
    try:
        sent = get(port, "/api/worm/W0001")[1]
        assert sent["score"] is None                                    # read before the other save landed
        pr.load = real
        now = get(port, "/api/worm/W0001/score-version")[1]["version"]
        assert sent["score_v"] != now                                   # so this window learns of the other save
    finally:
        srv.shutdown()
        srv.server_close()
