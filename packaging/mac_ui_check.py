"""Drive the running wormscore app's home and scoring pages in WebKit (Safari's engine), save
screenshots and fail on page errors. The mac-ui workflow runs it against the built Mac app, so
the Mac screens can be checked without a Mac. It works the same against the Windows app.

Start the app first (it serves the home page on port 47311), with a demo project that has a
blind set, then:  python packaging/mac_ui_check.py PROJECT OUT_DIR

Needs:  pip install playwright  and  python -m playwright install webkit
It selects PROJECT in the app's settings (~/.wormscore.json), sets the theme and opens the
scoring page; it never saves a score or an outline.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HOME = "http://127.0.0.1:47311"
SIZES = [(1440, 900), (1280, 800)]          # 15 and 13 inch MacBook windows


def post(route, body):
    req = urllib.request.Request(f"{HOME}/api/{route}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def wait_for_app(seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{HOME}/api/state", timeout=2) as r:
                return json.load(r)
        except OSError:
            time.sleep(1)
    sys.exit(f"wormscore did not answer on {HOME}")


def main(project, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    state = wait_for_app()
    print("app version", state["version"])
    # with its own windows (Windows), a page without this mark says the app moved to its window
    home_url = HOME + ("/?window=own" if state.get("own_window") else "")
    post("select", {"path": str(Path(project).resolve())})
    post("theme", {"theme": "dark"})
    problems = []

    def watch(page, name):
        page.on("pageerror", lambda e: problems.append(f"{name}: page error: {e}"))
        page.on("console", lambda m: m.type == "error" and problems.append(f"{name}: console: {m.text}"))
        page.on("requestfailed", lambda r: problems.append(f"{name}: request failed: {r.url} {r.failure}"))
        page.on("response", lambda r: r.status >= 400 and problems.append(f"{name}: HTTP {r.status} {r.url}"))

    def shot(page, name):
        page.screenshot(path=out / f"{name}.png")
        wide = page.evaluate("document.scrollingElement.scrollWidth - innerWidth")
        if wide > 0:
            problems.append(f"{name}: page scrolls sideways by {wide} px")
        print("saved", name)

    with sync_playwright() as p:
        browser = p.webkit.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        home = ctx.new_page()
        watch(home, "home")
        home.goto(home_url)
        home.wait_for_selector("#main:not(:has(.loading))")
        home.wait_for_function("document.querySelector('#ver').textContent.trim() !== ''")
        for w, h in SIZES:
            home.set_viewport_size({"width": w, "height": h})
            shot(home, f"home-dark-{w}")
        home.set_viewport_size({"width": 1440, "height": 900})
        home.click("#settingsBtn")
        home.wait_for_selector("#modal:not([hidden])")
        shot(home, "home-settings")
        home.keyboard.press("Escape")
        home.wait_for_selector("#modal[hidden]", state="attached")
        home.click("#themeBtn")
        home.wait_for_selector("html[data-theme=light]", state="attached")
        shot(home, "home-light-1440")
        home.click("#themeBtn")
        home.wait_for_selector("html[data-theme=dark]", state="attached")

        url = post("score", {})["url"]
        score = ctx.new_page()
        watch(score, "scoring")
        score.goto(url)
        score.wait_for_function("typeof st !== 'undefined' && st.code && st.mip && !st.loading", timeout=60000)
        lit = score.evaluate("""() => {
            const c = document.querySelector('#cv'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0;
            for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 60) n++;
            return n;
        }""")
        if lit < 100:
            problems.append(f"scoring: the image looks empty ({lit} lit pixels)")
        for w, h in SIZES:
            score.set_viewport_size({"width": w, "height": h})
            score.wait_for_timeout(300)       # the canvas redraws on the next frame
            shot(score, f"scoring-{w}")
        score.set_viewport_size({"width": 1440, "height": 900})
        score.click("[data-zoom='2']")
        score.wait_for_timeout(300)
        shot(score, "scoring-zoom2")
        score.click("[data-zoom='1']")

        # Save with nothing answered: nothing is saved, the questions to answer are outlined
        score.click("#scoreForm button[type=submit]")
        score.wait_for_selector("#scoreForm .missing")
        shot(score, "scoring-missing-answers")
        if score.evaluate("st.score"):
            problems.append("scoring: a score was saved with no answers")
        browser.close()

    if problems:
        print("\n".join(problems))
        sys.exit(f"{len(problems)} problem(s) on the pages")
    print("no page errors")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
