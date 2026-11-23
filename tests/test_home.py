import http.client
import json
import threading
import time

import pytest

from wormscore import home


@pytest.fixture
def desk(fresh_project, tmp_path, monkeypatch):
    """The home server on a free port, with its settings file in tmp_path and no real windows."""
    monkeypatch.setattr(home, "SETTINGS", tmp_path / "settings.json")
    opened, picks = [], []
    app = home.HomeApp(open_windows=False, pick=lambda title: picks.pop(0) if picks else None, opener=opened.append)
    srv = home.make_home_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"project": fresh_project, "port": srv.server_address[1], "app": app, "opened": opened, "picks": picks}
    app.stop_scoring()
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                 headers={"Content-Type": "application/json", **(headers or {})})
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, (json.loads(data) if data and res.getheader("Content-Type") == "application/json" else data)


def state(port):
    return call(port, "GET", "/api/state")[1]


def wait_for_job(port, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        job = state(port)["job"]
        if not job["running"]:
            return job
        time.sleep(0.2)
    raise AssertionError("step did not finish")


def test_home_page_and_state_show_counts_never_genotypes(desk):
    port = desk["port"]
    assert call(port, "GET", "/")[0] == 200
    assert call(port, "GET", "/static/home.js")[0] == 200
    assert state(port)["project"] is None
    assert call(port, "POST", "/api/select", {"path": str(desk["project"])})[0] == 200
    s = state(port)
    p = s["project"]
    assert (p["coded"], p["scored"], p["unblinded"], p["config_error"]) == (6, 0, False, None)
    assert p["scanned"] == 6
    text = json.dumps(s)
    for leak in ("control", "mutant", "genotype", "key.csv"):
        assert leak not in text


def test_only_home_files_are_served(desk):
    port = desk["port"]
    for path in ("/static/app.js", "/static/../server.py", "/static/..%2Fhome.py", "/key/key.csv", "/api/nothing"):
        assert call(port, "GET", path)[0] == 404, path


def test_requests_from_other_sites_are_refused(desk):
    port = desk["port"]
    body = {"path": str(desk["project"])}
    assert call(port, "GET", "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert call(port, "POST", "/api/select", body, headers={"Host": "evil.example"})[0] == 403
    assert call(port, "POST", "/api/select", body, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(port, "POST", "/api/select", body, headers={"Content-Type": "text/plain"})[0] == 403
    assert state(port)["project"] is None


def test_scan_runs_in_the_background_with_progress(desk):
    port = desk["port"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    assert call(port, "POST", "/api/run", {"step": "scan"})[0] == 200
    job = wait_for_job(port)
    assert job["outcome"] == "done" and job["frac"] == 1.0
    assert any("raw images" in line for line in job["lines"])


def test_new_codes_are_refused_once_scoring_started(desk):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    (project / "scoring").mkdir(exist_ok=True)
    (project / "scoring" / "scores.json").write_text(json.dumps({"W0001": {"usable": "no"}}))
    assert state(port)["project"]["scoring_started"] is True
    call(port, "POST", "/api/run", {"step": "build", "mode": "reassign"})
    job = wait_for_job(port)
    assert job["outcome"] == "stopped"
    assert any("Refusing to reassign codes" in line for line in job["lines"])
    assert call(port, "POST", "/api/run", {"step": "build", "mode": "shuffle"})[0] == 400


def test_unblind_needs_the_finished_box(desk):
    port = desk["port"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    status, body = call(port, "POST", "/api/run", {"step": "unblind"})
    assert status == 400 and "finished scoring" in body["error"]
    assert not (desk["project"] / "outputs" / "unblinded").exists()


def test_scoring_server_starts_and_stops_for_a_rebuild(desk):
    port, app = desk["port"], desk["app"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    status, body = call(port, "POST", "/api/score", {})
    assert status == 200
    sport = int(body["url"].rsplit(":", 1)[1])
    assert call(sport, "GET", "/api/ping")[0] == 200
    assert state(port)["scoring_url"] == body["url"]
    call(port, "POST", "/api/run", {"step": "build", "mode": "keep"})
    assert app.scoring is None                      # no scoring while the blind set is rebuilt
    wait_for_job(port, timeout=300)


def test_open_only_targets_inside_the_project(desk):
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    assert call(port, "POST", "/api/open", {"what": "config"})[0] == 200
    assert desk["opened"] == [project / "config.json"]
    assert call(port, "POST", "/api/open", {"what": "results"})[0] == 400       # not unblinded yet
    assert call(port, "POST", "/api/open", {"what": "C:/Windows/System32/calc.exe"})[0] == 400


def test_new_project_writes_a_template_and_recent_list_is_kept(desk, tmp_path):
    port = desk["port"]
    new = tmp_path / "new_project"
    new.mkdir()
    desk["picks"].append(str(new))
    assert call(port, "POST", "/api/new-project", {})[0] == 200
    assert (new / "config.json").is_file()
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    recent = [p["path"] for p in state(port)["recent"]]
    assert recent[:2] == [str(desk["project"]), str(new)]
    call(port, "POST", "/api/forget", {"path": str(new)})
    assert [p["path"] for p in state(port)["recent"]] == [str(desk["project"])]


def test_icons_and_manifest_are_served(desk):
    port = desk["port"]
    for path in ("/static/icon-192.png", "/static/icon-512.png", "/static/manifest.webmanifest"):
        assert call(port, "GET", path)[0] == 200, path


def test_guide_opens_in_the_default_browser(desk, monkeypatch):
    opened = []
    monkeypatch.setattr(home.webbrowser, "open", opened.append)
    assert call(desk["port"], "POST", "/api/guide", {})[0] == 200
    assert opened == [home.GUIDE_URL]


def test_app_icon_ships_with_every_size_the_taskbar_needs():
    from PIL import Image
    ico = Image.open(home.STATIC / "wormscore.ico")
    assert {(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)} <= set(ico.info["sizes"])
    assert "wormscore.home" in home._relaunch_command() or home._relaunch_command().endswith('.exe"')


def test_remove_from_list_matches_any_path_spelling(desk):
    """A settings file can hold C:/a/b while the page sends C:\a\b; the X must still remove it."""
    port, proj = desk["port"], desk["project"]
    home.SETTINGS.write_text(json.dumps({"project": proj.as_posix(), "recent": [proj.as_posix()]}), encoding="utf-8")
    shown = state(port)["recent"][0]["path"]
    assert shown == str(proj)
    assert call(port, "POST", "/api/forget", {"path": shown})[0] == 200
    s = home.load_settings()
    assert s["recent"] == [] and s["project"] == ""


def test_app_stays_open_while_its_window_exists(monkeypatch):
    """A minimized window can stop checking in; the app must not close while the window is there."""
    app = home.HomeApp(open_windows=False)
    now = time.time()
    app.last_seen = now - 3600                                   # page quiet for an hour
    monkeypatch.setattr(home, "WINDOWS", {"tracking": True, "last": now - 5})
    assert not home.should_close(app, now)                       # window still there
    monkeypatch.setattr(home, "WINDOWS", {"tracking": True, "last": now - 60})
    assert home.should_close(app, now)                           # window closed a minute ago
    app.job.running = True
    assert not home.should_close(app, now)                       # never during a step
    app.job.running = False
    monkeypatch.setattr(home, "WINDOWS", {"tracking": False, "last": 0.0})
    app.last_seen = now - 600
    assert not home.should_close(app, now)                       # no tracking: wait much longer
    app.last_seen = now - 4000
    assert home.should_close(app, now)


def test_app_log_records_why_it_closed(tmp_path, monkeypatch):
    from wormscore import update as U
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path)
    app = home.HomeApp(open_windows=False)
    app.last_seen = time.time() - 120
    monkeypatch.setattr(home, "WINDOWS", {"tracking": True, "last": time.time() - 90})
    home.log_event(home.close_reason(app))
    line = (tmp_path / "app.log").read_text(encoding="utf-8").strip()
    assert "closing: last window seen 90 s ago, page quiet 120 s" in line


def test_waking_from_sleep_never_closes_the_app(tmp_path, monkeypatch):
    """Lid closed for an hour: on waking every timer looks stale, but the app must stay."""
    from wormscore import update as U
    monkeypatch.setattr(U, "data_dir", lambda: tmp_path)
    app = home.HomeApp(open_windows=False)
    now = time.time()
    app.last_seen = now - 3600
    monkeypatch.setattr(home, "WINDOWS", {"tracking": True, "last": now - 3600})
    assert not home.watchdog_step(app, tick=now - 3600, now=now, window_open=lambda: False)
    assert "woke after about 3600 s asleep" in (tmp_path / "app.log").read_text(encoding="utf-8")
    # an ordinary check: windows gone a minute ago, but one is found when looking again
    app.last_seen = now - 60
    home.WINDOWS["last"] = now - 60
    assert not home.watchdog_step(app, tick=now - 5, now=now, window_open=lambda: True)
    # windows really gone: close
    home.WINDOWS["last"] = now - 60
    assert home.watchdog_step(app, tick=now - 5, now=now, window_open=lambda: False)


def test_a_link_start_never_runs_a_command(monkeypatch):
    """A web page chooses the wormscore:// link, so a link start opens the app window only."""
    for argv in (["--from-link", "wormscore://start/"],
                 ["--from-link", 'wormscore://x" unblind C:/p --i-have-finished-scoring "'],
                 ["wormscore://start", "prepare", "C:/p", "--reassign-codes"],
                 ["prepare", "C:/p", "WORMSCORE:evil"]):
        assert home.link_launch(argv), argv
    assert not home.link_launch(["prepare", "C:/p"]) and not home.link_launch([])
    import runpy
    import sys
    from pathlib import Path
    called = []
    monkeypatch.setattr(sys, "argv", ["wormscore.exe", "--from-link", "wormscore://x", "unblind", "C:/p"])
    monkeypatch.setattr("wormscore.cli.main", lambda *a, **k: called.append("cli"))
    monkeypatch.setattr("wormscore.home.main", lambda *a, **k: called.append("home"))
    runpy.run_path(str(Path(__file__).parents[1] / "packaging" / "wormscore_app.py"), run_name="__main__")
    assert called == ["home"] and sys.argv == ["wormscore.exe"]


def test_an_unexpected_error_is_shown_not_read_as_a_lost_connection(desk, tmp_path, monkeypatch):
    """A folder that cannot be written answers with a message, not a dropped connection."""
    logged = []
    monkeypatch.setattr(home, "log_event", logged.append)        # never the real app.log
    desk["picks"].append(str(tmp_path / "does" / "not" / "exist"))
    status, body = call(desk["port"], "POST", "/api/new-project", {})
    assert status == 500 and body["ok"] is False and "did not work" in body["error"]
    assert state(desk["port"])["version"]          # the app is still running
    assert logged and logged[0].startswith("error: FileNotFoundError")


def test_the_project_list_never_waits_for_the_image_libraries(fresh_project):
    """The home screen's status loads no numpy or scipy (they take seconds to load in the app), and
    the page carries the version from the start (1.6.3)."""
    import subprocess
    from pathlib import Path
    import sys
    code = ("import sys; from pathlib import Path; from wormscore import home; "
            f"home.project_status(Path(r'{fresh_project}')); "
            "print(sorted(m for m in ('numpy', 'scipy', 'PIL', 'tifffile') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=Path(home.__file__).parents[1])
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


def test_home_page_has_the_version_before_any_script_runs(desk):
    from wormscore import __version__
    st, page = call(desk["port"], "GET", "/")
    assert st == 200 and f'id="ver">{__version__}</span>'.encode() in page
    assert b'<html lang="en" data-theme="dark">' in page        # the saved theme (dark by default)


def _score_all(project):
    from wormscore.server import Project
    p = Project(project)
    for d in sorted(project.joinpath("blind").iterdir()):
        p.save_score(d.name, {"usable": "yes", "cells_seen": "2", "n1_call": "normal", "n2_call": "dim",
                              "confidence": "sure", "flag_answers": {}, "count_this_image": "yes"})


def test_results_card_numbers_come_from_the_results_tables_not_the_key(desk):
    from wormscore.pipeline import read_csv
    from wormscore.unblind import unblind
    port, project = desk["port"], desk["project"]
    call(port, "POST", "/api/select", {"path": str(project)})
    assert state(port)["project"]["results"] is None          # nothing to show before Unblind
    assert home.results_summary(project, 0, 6) is None
    _score_all(project)
    unblind(project, finished=True, anyway=True, log=lambda *a: None)
    r = state(port)["project"]["results"]
    assert (r["scored"], r["coded"]) == (6, 6)
    # The summary works without the key: it reads only the unblinded results tables.
    import shutil
    shutil.rmtree(project / "key")
    assert home.results_summary(project, 6, 6) == r
    table = [x for x in read_csv(project / "outputs" / "unblinded" / "penetrance.csv") if x.get("genotype")]
    assert sorted((x["genotype"], x["animals"], x["affected"]) for x in r["penetrance"]) \
        == sorted((x["genotype"], int(x["animals"]), int(x["animals_any_affected"])) for x in table)
    assert r["tests"] and all("p" in t and " vs " not in t["a"] for t in r["tests"])


def test_help_definitions_are_served_to_every_window(desk):
    from wormscore import browse
    from wormscore.server import make_server
    assert call(desk["port"], "GET", "/static/terms.js")[0] == 200
    project = desk["project"]
    for srv in (make_server(project, port=0), None):
        if srv is None:          # Browse: open after Unblind
            from wormscore.unblind import unblind
            _score_all(project)
            unblind(project, finished=True, anyway=True, log=lambda *a: None)
            srv = browse.make_server(project, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            status, data = call(srv.server_address[1], "GET", "/static/terms.js")
            assert status == 200 and b"WS_TERMS" in data
            assert b"terms.js" in call(srv.server_address[1], "GET", "/")[1]
        finally:
            srv.shutdown()
            srv.server_close()


def test_a_finished_step_keeps_the_time_it_took(monkeypatch):
    """Before 1.13.0 "Done in" kept counting after a step ended, and the home page redrew on every
    poll because the state changed."""
    import time as _time
    job = home.Job()
    now = [1000.0]
    monkeypatch.setattr(home.time, "time", lambda: now[0])
    assert job.start("Quick step", "p", lambda: None)
    for _ in range(200):
        if not job.running:
            break
        _time.sleep(0.01)
    assert not job.running and job.outcome == "done"
    first = job.snapshot(0)["elapsed_s"]
    now[0] += 60
    assert job.snapshot(0)["elapsed_s"] == first == 0


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_ticking_the_update_warning_box_keeps_the_notes_scrolled_and_the_focus(desk, monkeypatch, engine):
    # 1.26.26: ticking "I understand..." rebuilt the dialog, so the notes went back to the top and the
    # keyboard focus jumped to "Not now"
    sync_api = pytest.importorskip("playwright.sync_api")
    notes = "\n".join(f"- Change number {i}, a line long enough to take some room in the dialog." for i in range(60))
    fake = {"kind": "windows", "enabled": True, "checking": False, "error": None, "latest": "9.9.9", "newer": True,
            "show": True, "notes": notes, "page": "", "can_install": True, "pip_command": "", "phase": None,
            "warning": "Scoring is in progress in Demo.", "frac": None, "message": "", "result": None, "previous": None}
    monkeypatch.setattr(desk["app"], "update_view", lambda statuses: dict(fake))
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as exc:
            pytest.skip(f"{engine} is not installed for Playwright ({type(exc).__name__})")
        try:
            page = b.new_page(viewport={"width": 1200, "height": 800})
            page.goto(f"http://127.0.0.1:{desk['port']}/")
            page.wait_for_selector("#updModal:not([hidden]) .ud-notes")
            page.evaluate("document.querySelector('.ud-notes').scrollTop = 300")
            update = page.get_by_role("button", name="Update anyway")
            assert update.is_disabled()
            page.locator(".ud-agree input").click()
            assert update.is_enabled()
            page.wait_for_timeout(1500)          # a few polls of the state
            assert page.evaluate("document.querySelector('.ud-notes').scrollTop") == 300
            # by keyboard: the focus stays on the box (a click does not focus a box in WebKit)
            page.locator(".ud-agree input").focus()
            page.keyboard.press("Space")
            assert update.is_disabled()
            page.wait_for_timeout(1500)
            assert page.evaluate("document.activeElement.type") == "checkbox"
            assert page.evaluate("document.querySelector('.ud-notes').scrollTop") == 300
        finally:
            b.close()


PLACE = """() => { const a = document.activeElement;
  const same = [...document.querySelectorAll('#main input[type=checkbox]')].filter(x => x.closest('label')?.textContent.trim() === a.closest('label')?.textContent.trim());
  return {y: Math.round(a.getBoundingClientRect().top), name: a.closest('label')?.textContent.trim() || a.id, nth: same.indexOf(a)}; }"""


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_changing_a_setting_keeps_the_page_where_it_was_and_the_focus_on_the_control(desk, engine):
    # 1.26.26: every change in Project settings and the criteria editor drew the form again, which
    # scrolled the page up (by up to a thousand pixels) and dropped the keyboard focus. The control
    # now keeps its place on the screen (the page scrolls by whatever grew above it) and the focus.
    sync_api = pytest.importorskip("playwright.sync_api")
    port = desk["port"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as exc:
            pytest.skip(f"{engine} is not installed for Playwright ({type(exc).__name__})")
        try:
            page = b.new_page(viewport={"width": 1400, "height": 600})
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("st.data && st.data.project")
            # Project settings: a box in "More planned tests", by keyboard (WebKit does not focus a clicked box)
            page.evaluate("openProjectSettings('folders')")
            page.locator("summary", has_text="More planned tests").click()
            box = page.locator("#main label", has_text="dim").locator("input[type=checkbox]").first
            box.scroll_into_view_if_needed()
            box.focus()
            before = page.evaluate(PLACE)
            assert page.evaluate("document.querySelector('.main').scrollTop") > 300
            page.keyboard.press("Space")
            page.wait_for_timeout(300)
            assert page.evaluate(PLACE) == before
            # after a Remove button the keyboard goes to that list's Add button, never to another
            # list's Remove button with the same name (Enter again would remove a rule nobody chose)
            page.evaluate("document.querySelector('.ps details.lo').open = true")
            for _ in range(2):
                page.locator(".ps details.lo button", has_text="Add a rule").first.focus()
                page.keyboard.press("Enter")
                page.wait_for_timeout(200)
            genos = page.locator(".ps .rules").first.locator("button.rm")
            genos.nth(genos.count() - 1).focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            assert page.evaluate("document.activeElement.textContent.trim()") == "Add genotype rule"
            assert page.evaluate("!document.activeElement.closest('details')")
            # the criteria editor: the second of three boxes with the same name ("defect")
            page.evaluate("openProjectSettings('form')")
            page.wait_for_function("document.querySelectorAll('#main .sf-check input').length > 2")
            boxes = page.locator("#main label", has_text="defect").locator("input[type=checkbox]")
            boxes.nth(1).scroll_into_view_if_needed()
            boxes.nth(1).focus()
            before = page.evaluate(PLACE)
            assert before["nth"] == 1 and page.evaluate("document.querySelector('.main').scrollTop") > 300
            page.keyboard.press("Space")
            page.wait_for_timeout(300)
            assert page.evaluate(PLACE) == before
        finally:
            b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_click_or_tab_right_after_typing_in_the_criteria_is_not_lost(desk, engine):
    # 1.26.26: leaving a text box such as "Call 2" drew the criteria again at once, which replaced the
    # button being clicked (the click did nothing) or the control Tab was moving to (focus was lost)
    sync_api = pytest.importorskip("playwright.sync_api")
    port = desk["port"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    calls = "[...document.querySelectorAll('[aria-label^=\"Call \"]')].map(e => e.value)"
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as exc:
            pytest.skip(f"{engine} is not installed for Playwright ({type(exc).__name__})")
        try:
            page = b.new_page(viewport={"width": 1400, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("st.data && st.data.project")
            page.evaluate("openProjectSettings('form')")
            page.wait_for_selector("[aria-label='Call 1']")
            before = page.evaluate(calls)
            page.locator("[aria-label='Call 2']").click()
            page.keyboard.press("End")
            page.keyboard.type("z")
            page.locator("[aria-label='Remove call 1']").click()
            page.wait_for_timeout(300)
            assert page.evaluate(calls) == [before[1] + "z", *before[2:]]
            page.locator("[aria-label='Call 1']").focus()
            page.keyboard.press("End")
            page.keyboard.type("y")
            page.keyboard.press("Tab")
            page.wait_for_timeout(300)
            assert page.evaluate("document.activeElement !== document.body && !!document.activeElement.closest('#main')")
            assert page.evaluate(calls)[0] == before[1] + "zy"
        finally:
            b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_running_step_updates_in_place_and_a_log_being_read_stays_put(desk, engine):
    # 1.26.26: the home page rebuilt its main area and the project list on every poll while a step
    # ran (about every 0.7 s), so a log scrolled up to read an earlier line jumped back to its end
    sync_api = pytest.importorskip("playwright.sync_api")
    port, app = desk["port"], desk["app"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})

    def slow():
        for i in range(40):
            app.job.say(f"step line {i}")
            app.job.progress(i + 1, 40)
            time.sleep(0.2)

    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as exc:
            pytest.skip(f"{engine} is not installed for Playwright ({type(exc).__name__})")
        try:
            page = b.new_page(viewport={"width": 1400, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("st.data && st.data.project")
            page.evaluate("""() => { window.__side = 0;
                new MutationObserver(() => window.__side++).observe(document.querySelector('#recent'), { childList: true }); }""")
            assert app.job.start("Slow test step", desk["project"], slow)
            page.wait_for_selector(".activity")
            page.locator("[data-k=log]").click()
            page.wait_for_function("st.data.job.running && st.log.length > 12", timeout=15000)
            page.evaluate("document.querySelector('#log').scrollTop = 0")
            meta = page.locator(".a-meta").text_content()
            lines = page.evaluate("st.log.length")
            page.wait_for_function(f"st.log.length > {lines + 6}", timeout=15000)
            page.wait_for_timeout(300)
            assert page.evaluate("document.querySelector('#log').scrollTop") == 0           # still being read
            assert page.evaluate("document.querySelector('#log').textContent.split('\\n').length") == page.evaluate("st.log.length")
            assert page.locator(".a-meta").text_content() != meta                             # the numbers move
            assert page.evaluate("document.querySelector('#stepStatus')?.textContent") == "Slow test step started."
            # scrolled to its end, the log follows new lines
            page.evaluate("const l = document.querySelector('#log'); l.scrollTop = l.scrollHeight")
            n = page.evaluate("st.log.length")
            page.wait_for_function(f"st.log.length > {n + 4}", timeout=15000)
            page.wait_for_timeout(300)
            assert page.evaluate("const l = document.querySelector('#log'); l.scrollTop + l.clientHeight >= l.scrollHeight - 8")
            page.wait_for_function("!st.data.job.running", timeout=20000)
            page.wait_for_function("document.querySelector('#stepStatus').textContent === 'Slow test step done.'")
            assert page.evaluate("window.__side") == 0          # the project list was never rebuilt
        finally:
            b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_a_welcome_tile_is_a_button_that_space_presses(desk, engine):
    # 1.26.26: the tiles were divs with role=button that answered Enter only
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as exc:
            pytest.skip(f"{engine} is not installed for Playwright ({type(exc).__name__})")
        try:
            page = b.new_page(viewport={"width": 1400, "height": 800})
            page.goto(f"http://127.0.0.1:{desk['port']}/")
            tile = page.locator("[data-k='tile:demo']")
            assert tile.evaluate("t => t.tagName") == "BUTTON"
            # where a row of tiles is stretched to its tallest (950 px: three tiles, one taller), the
            # text stays at the top as it did in the old cards; title and description keep their colours
            page.set_viewport_size({"width": 950, "height": 800})
            page.wait_for_timeout(100)
            tops = page.evaluate("""() => [...document.querySelectorAll('.tile')].map(t =>
                [Math.round(t.querySelector('b').getBoundingClientRect().top - t.getBoundingClientRect().top), Math.round(t.getBoundingClientRect().height)])""")
            assert len({h for _, h in tops}) == 1 and {y for y, _ in tops} == {17}
            assert page.evaluate("""() => { const t = document.querySelector('.tile');
                return getComputedStyle(t.querySelector('b')).color !== getComputedStyle(t.querySelector('.tile-d')).color; }""")
            tile.focus()
            page.keyboard.press("Space")
            page.wait_for_selector("#modal:not([hidden])")
            assert "demo" in page.locator("#modal").inner_text().lower()
        finally:
            b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_check_now_in_settings_gives_the_keyboard_back(desk, monkeypatch, engine):
    # Check now is off while it checks, and a button turned off loses the keyboard
    sync_api = pytest.importorskip("playwright.sync_api")
    fake = {"kind": "windows", "enabled": True, "checking": False, "error": None, "latest": "1.0.0", "newer": False,
            "show": False, "notes": "", "page": "", "can_install": True, "pip_command": "", "phase": None,
            "warning": "", "frac": None, "message": "", "result": None, "previous": None}
    monkeypatch.setattr(desk["app"], "update_view", lambda statuses: dict(fake))
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as exc:
            pytest.skip(f"{engine} is not installed for Playwright ({type(exc).__name__})")
        try:
            page = b.new_page(viewport={"width": 1200, "height": 800})
            held = []
            page.route("**/api/update", lambda route: held.append(route))      # no request to GitHub
            page.goto(f"http://127.0.0.1:{desk['port']}/")
            page.wait_for_function("st.data")
            page.evaluate("() => { openSettings(); }")      # not awaited: it ends when the dialog closes
            check = page.get_by_role("button", name="Check now")
            check.focus()
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelector('#modal button[disabled]')")
            for _ in range(50):
                if held:
                    break
                page.wait_for_timeout(50)
            held[0].fulfill(json={})
            page.wait_for_function("[...document.querySelectorAll('#modal button')].some(b => b.textContent === 'Check now' && !b.disabled)")
            assert page.evaluate("document.activeElement.textContent") == "Check now"
        finally:
            b.close()


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_adding_a_reference_image_stays_on_its_row_when_a_row_above_is_removed(desk, engine, tmp_path):
    # "Adding..." was kept by the row's label ("Call 2"): removing Call 1 during the upload moved it to another row
    sync_api = pytest.importorskip("playwright.sync_api")
    from PIL import Image
    png = tmp_path / "ref.png"
    Image.new("RGB", (8, 8), "white").save(png)
    port = desk["port"]
    call(port, "POST", "/api/select", {"path": str(desk["project"])})
    busy = "[...document.querySelectorAll('.sf-add-img')].filter(b => b.disabled).map(b => b.getAttribute('aria-label'))"
    with sync_api.sync_playwright() as p:
        try:
            b = getattr(p, engine).launch()
        except Exception as exc:
            pytest.skip(f"{engine} is not installed for Playwright ({type(exc).__name__})")
        try:
            page = b.new_page(viewport={"width": 1400, "height": 800})
            held = []
            page.route("**/api/project-settings", lambda route: held.append(route)
                       if "criteria-image" in (route.request.post_data or "") else route.continue_())
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("st.data && st.data.project")
            page.evaluate("openProjectSettings('form')")
            page.wait_for_selector("[aria-label='Call 1']")
            for n in (1, 2):
                add_def = page.locator(f"[aria-label='Add a definition for Call {n}']")
                if add_def.count():
                    add_def.click()
            with page.expect_file_chooser() as fc:
                page.locator("[aria-label='Add a reference image for Call 2']").click()
            fc.value.set_files(str(png))
            page.wait_for_function(f"{busy}.length === 1")
            assert page.evaluate(busy) == ["Add a reference image for Call 2"]
            page.locator("[aria-label='Remove call 1']").click()
            page.wait_for_timeout(300)
            assert page.evaluate(busy) == ["Add a reference image for Call 1"]       # the same call, now first
            for _ in range(100):
                if held:
                    break
                page.wait_for_timeout(50)
            held[0].fulfill(json={"path": "criteria/ref.png"})
            page.wait_for_function(f"{busy}.length === 0")
            assert page.locator("[aria-label='Show the reference image for Call 1']").count() == 1
        finally:
            b.close()
