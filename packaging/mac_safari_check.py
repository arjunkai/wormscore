"""Load the running wormscore app's home and scoring pages in real Safari (through safaridriver) and
fail if either does not draw. The Mac app opens in the user's default browser, usually Safari, and
Playwright's WebKit (mac_ui_check.py) is the same engine but not the same browser.

Start the app first, with a demo project that has a blind set, then:
    python packaging/mac_safari_check.py PROJECT
Needs:  pip install selenium  and, once per machine,  sudo safaridriver --enable
It saves nothing: no score, no outline.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver

HOME = "http://127.0.0.1:47311"
LIT = """const c = document.querySelector('#cv');
const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
let n = 0;
for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 60) n++;
return n;"""


def post(route, body):
    req = urllib.request.Request(f"{HOME}/api/{route}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def until(driver, script, what, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        try:
            got = driver.execute_script(script)
        except Exception:        # the page is still loading
            got = None
        if got:
            return got
        time.sleep(0.5)
    explain(driver, what)
    sys.exit(f"Safari: {what}")


# Said when a check fails, so a failure on GitHub's Mac can be understood without a Mac: the page's state,
# whether Safari can parse each of its scripts, and a screenshot (uploaded with the mac-ui screenshots).
STATE = """const c = document.querySelector('#cv');
return {url: location.href, title: document.title, ready: document.readyState,
        st: typeof st === 'undefined' ? 'no st' : {code: st.code, mip: !!st.mip, loading: st.loading},
        canvas: c ? [c.width, c.height, c.clientWidth, c.clientHeight] : 'none',
        text: (document.body ? document.body.innerText : '').slice(0, 400)};"""
PARSE = """const done = arguments[arguments.length - 1];
Promise.all([...document.scripts].filter(s => s.src).map(s => fetch(s.src).then(r => r.text()).then(t => {
  try { new Function(t); return s.src + ': parses'; } catch (e) { return s.src + ': ' + e; }
}))).then(done, e => done(String(e)));"""


# Whether the window gets animation frames (a page draws on the next frame; a hidden window may get none),
# and what the canvas holds when the page draws at once, without waiting for a frame.
FRAMES = """const done = arguments[arguments.length - 1];
const pending = typeof st !== 'undefined' ? !!st.drawFrame : 'no st';
let got = false;
requestAnimationFrame(() => { got = true; });
setTimeout(() => done({drawPending: pending, frameWithin3s: got, hidden: document.hidden,
                       visibility: document.visibilityState, focus: document.hasFocus()}), 3000);"""
DRAWN_NOW = "if (typeof draw !== 'function') return 'no draw'; draw(); " + LIT


def explain(driver, what):
    for name, run in (("state", lambda: driver.execute_script(STATE)),
                      ("scripts", lambda: driver.execute_async_script(PARSE)),
                      ("frames", lambda: driver.execute_async_script(FRAMES)),
                      ("lit after drawing at once", lambda: driver.execute_script(DRAWN_NOW))):
        try:
            print(f"{name}: {json.dumps(run(), indent=1)}")
        except Exception as exc:
            print(f"{name}: could not be read ({str(exc).splitlines()[0]})")
    try:
        Path("screenshots").mkdir(exist_ok=True)
        driver.save_screenshot("screenshots/safari-failed.png")
        print("screenshot: screenshots/safari-failed.png")
    except Exception as exc:
        print(f"screenshot: none ({str(exc).splitlines()[0]})")
    sys.stdout.flush()


def main(project):
    post("select", {"path": str(Path(project).resolve())})
    driver = None
    for attempt in range(3):        # the first session after "safaridriver --enable" often times out
        try:
            driver = webdriver.Safari()
            break
        except Exception as exc:
            print(f"Safari did not start (attempt {attempt + 1}): {str(exc).splitlines()[0]}")
            time.sleep(10)
    if driver is None:
        sys.exit("Safari: no automation session could be started on this machine")
    try:
        print("Safari", driver.capabilities.get("browserVersion"), flush=True)
        driver.set_script_timeout(30)
        driver.get(HOME)
        # a script Safari cannot parse leaves the page on its loading screen, with no step cards
        until(driver, "return document.querySelectorAll('ol.steps > li').length >= 5", "the home page did not draw its steps")
        url = post("score", {})["url"]
        driver.get(url)
        # as in mac_ui_check.py: wait for the image to be loaded, then for it to be drawn
        until(driver, "return typeof st !== 'undefined' && st.code && st.mip && !st.loading",
              "the scoring page did not load its image")
        lit = until(driver, LIT, "the scoring page drew no image")
        print("scoring page: lit pixels", lit)
        until(driver, "return document.querySelectorAll('#list button, nav button').length > 0",
              "the scoring page listed no codes")
    finally:
        driver.quit()
    print("Safari: home and scoring pages drew")


if __name__ == "__main__":
    main(sys.argv[1])
