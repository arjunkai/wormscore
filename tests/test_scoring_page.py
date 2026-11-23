"""The scoring page in real browser engines (Chromium, as in the Windows app, and WebKit, Safari's engine).

Needs Playwright with its browsers (pip install playwright; python -m playwright install chromium webkit).
Without them these tests are skipped.
"""
import threading

import pytest

from wormscore.server import make_server

sync_api = pytest.importorskip("playwright.sync_api")

# What Safari Private windows, Brave and Firefox's fingerprinting protection do: values read back
# from a canvas come with a little noise.
NOISE = """
const real = CanvasRenderingContext2D.prototype.getImageData;
CanvasRenderingContext2D.prototype.getImageData = function (...a) {
  const d = real.apply(this, a);
  for (let i = 0; i < d.data.length; i += 4 * 37) d.data[i + 1] ^= 1;
  return d;
};
"""


@pytest.fixture(scope="module")
def playwright():
    with sync_api.sync_playwright() as p:
        yield p


@pytest.fixture(params=["chromium", "webkit"])
def browser(request, playwright):
    try:
        b = getattr(playwright, request.param).launch()
    except Exception as exc:          # the engine is not installed on this computer
        pytest.skip(f"{request.param} is not installed for Playwright ({type(exc).__name__})")
    yield b
    b.close()


@pytest.fixture
def url(fresh_project):
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()
    srv.server_close()


def open_page(browser, url, script=None):
    page = browser.new_page(viewport={"width": 1536, "height": 785})
    if script:
        page.add_init_script(script)
    page.goto(url)
    page.wait_for_function("document.querySelector('#codeTitle').textContent.startsWith('W')")
    return page


def test_no_warning_when_the_browser_hands_back_the_image_data_unchanged(browser, url):
    page = open_page(browser, url)
    assert page.evaluate("st.readbackAltered") is False
    assert not page.locator("#readbackWarn").is_visible()


def test_a_window_that_changes_image_data_says_so_above_the_image(browser, url):
    page = open_page(browser, url, NOISE)
    warn = page.locator("#readbackWarn")
    assert warn.is_visible()
    text = warn.inner_text()
    assert "changes image data" in text and "normal window" in text
    assert warn.locator(".sr").text_content() == "Warning: "
    # in the card it concerns: directly above the image, and it stays when another image is opened
    box, canvas = warn.bounding_box(), page.locator("#canvasWrap").bounding_box()
    assert box["y"] + box["height"] <= canvas["y"] + 1 and box["height"] <= 56      # at most two lines of text
    page.keyboard.press("n")
    page.wait_for_function("st.code !== st.worms[0].code")
    assert warn.is_visible()


def test_number_keys_answer_after_a_click_on_an_answer(browser, url):
    """Safari does not move the keyboard focus to a radio button that is clicked, so the number keys
    did nothing after a click (they act on the question that has the focus)."""
    page = open_page(browser, url)
    checked = lambda name: page.evaluate(f"document.querySelector('#scoreForm').elements['{name}'].value")
    page.locator("label", has_text="Fairly sure").click()
    assert checked("confidence") == "fairly sure"
    page.keyboard.press("3")
    assert checked("confidence") == "unsure"
    # a click on the button itself, then the keys carry on with the next question
    page.locator("input[name=usable][value=yes]").click()
    page.keyboard.press("1")
    assert checked("usable") == "yes"
    assert page.evaluate("document.activeElement.type") == "radio"
    assert page.evaluate("document.activeElement.name") != "usable"


def test_save_outlines_says_saving_at_once(browser, url):
    """Measuring a large stack takes a few seconds; nothing showed meanwhile."""
    page = open_page(browser, url)
    held = []
    page.route("**/rois", lambda route: held.append(route))          # the save is kept waiting until the test lets it go
    # outlines open once the image has a score
    page.evaluate("api(`/api/worm/${st.code}/score`, {usable: 'no', unusable_reason: 'x', flag_answers: {}})"
                  ".then(() => openWorm(st.code))")
    page.wait_for_function("document.querySelector('#markTab').getAttribute('aria-disabled') !== 'true'")
    page.locator("#markTab").click()
    page.locator("#sugList button").first.click()
    assert page.locator("#roiMsg").inner_text() == "Unsaved changes"
    page.locator("#saveRois").click()
    assert page.locator("#roiMsg").inner_text() == "Saving..."
    assert page.locator("#saveRois").is_disabled()
    held[0].continue_()
    page.wait_for_function("document.querySelector('#roiMsg').textContent === 'Saved 1 outline'")
    page.wait_for_function("!document.querySelector('#saveRois').disabled")     # on again once the list is reloaded
    assert page.evaluate("st.roisDirty") is False


# ------------------------------------------------------------------ Browse images

LAYOUT = """() => {
  const box = s => { const r = document.querySelector(s).getBoundingClientRect();
                     return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; };
  const panel = document.querySelector('#panel'), wrap = document.querySelector('.m-wrap');
  return {panel: box('#panel'), next: box('#nextBtn'), title: box('#codeTitle'),
          panelScroll: [panel.scrollWidth, panel.clientWidth, panel.scrollLeft],
          table: wrap ? box('.m-table') : null, wrapScroll: wrap ? [wrap.scrollWidth, wrap.clientWidth] : null,
          actions: box('.tab[data-tab=mark] .actions')};
}"""


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_browse_fits_a_laptop_screen_at_125_percent(browser, scheme, tmp_path):
    """1536 x 785 is a 1920 x 1080 laptop at Windows' default 125% scaling. Next image ran past the edge
    of the window, and the measurement table was wider than its panel and below what was shown."""
    from wormscore import browse
    from wormscore.demo import make_demo
    from wormscore.pipeline import inventory
    project = tmp_path / "p"
    project.mkdir()
    make_demo(project, n_per_group=2, log=lambda *a: None)
    inventory(project, log=lambda *a: None)
    srv = browse.make_server(project, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctx = browser.new_context(viewport={"width": 1536, "height": 785}, device_scale_factor=1.25, color_scheme=scheme)
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
        page.wait_for_function("typeof st !== 'undefined' && st.meta && st.mip")
        at = page.evaluate(LAYOUT)
        assert at["next"]["right"] <= at["panel"]["right"] - 8, "Next image is cut off"
        assert at["title"]["right"] <= at["next"]["left"]
        assert at["panelScroll"][0] <= at["panelScroll"][1]
        # two suggested cells as Neuron 1 and Neuron 2, then Measure
        page.locator("#sugList .item").nth(0).locator("button").nth(0).click()
        page.locator("#sugList .item").nth(1).locator("button").nth(1).click()
        page.locator("#saveRois").click()
        page.wait_for_selector(".m-table")
        page.wait_for_timeout(200)
        at = page.evaluate(LAYOUT)
        assert at["panelScroll"][2] == 0 and at["panelScroll"][0] <= at["panelScroll"][1]
        assert at["wrapScroll"][0] <= at["wrapScroll"][1], "the table is wider than its panel"
        assert at["panel"]["left"] <= at["table"]["left"] and at["table"]["right"] <= at["panel"]["right"]
        assert at["table"]["top"] >= at["panel"]["top"] and at["table"]["bottom"] <= at["actions"]["top"] + 1, \
            "the table is not in view after Measure"
        text = page.locator(".m-table").inner_text()
        for word in ("Neuron 1", "Neuron 2", "Area", "Sharpest plane", "Saturated pixels", "Peak"):
            assert word in text
        # Next image is still in reach, above the numbers
        assert at["next"]["right"] <= at["panel"]["right"] - 8 and at["next"]["top"] >= at["panel"]["top"]
        assert at["next"]["bottom"] <= at["table"]["top"]
        ctx.close()
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------------------ two scoring windows, one image

def test_the_window_whose_outlines_were_not_saved_says_so(browser, fresh_project):
    """Two windows save outlines for one image; the first click is still being measured when the second
    is saved. The later click's outlines are the saved ones, and the first window says its own were not
    saved (it used to say "Saved 1 outline" while the file held the other window's, or the other way round)."""
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    measuring, go, real, calls = threading.Event(), threading.Event(), srv.project.measure_rois, []

    def slow(code, rois):
        calls.append(code)
        if len(calls) == 1:
            measuring.set()
            assert go.wait(60)
        return real(code, rois)
    srv.project.measure_rois = slow
    try:
        one = open_page(browser, url)
        one.evaluate("api(`/api/worm/${st.code}/score`, {usable: 'no', unusable_reason: 'x', flag_answers: {}})"
                     ".then(() => openWorm(st.code))")
        one.wait_for_function("document.querySelector('#markTab').getAttribute('aria-disabled') !== 'true'")
        two = open_page(browser, url)
        two.evaluate("code => openWorm(code)", one.evaluate("st.code"))      # it opened on the next unscored image
        two.wait_for_function("document.querySelector('#markTab').getAttribute('aria-disabled') !== 'true'")
        assert one.evaluate("st.code") == two.evaluate("st.code")
        for page, nth in ((one, 0), (two, 1)):
            page.locator("#markTab").click()
            page.locator("#sugList button").nth(nth).click()
        one.locator("#saveRois").click()
        assert measuring.wait(30)
        two.locator("#saveRois").click()
        two.wait_for_function("document.querySelector('#roiMsg').textContent === 'Saved 1 outline'")
        go.set()
        one.wait_for_function("document.querySelector('#roiMsg').textContent.startsWith('Not saved')")
        assert one.locator("#roiMsg").inner_text() == "Not saved: another window saved this image's outlines just after you clicked; those are kept."
        assert one.evaluate("st.roisDirty") is True and two.evaluate("st.roisDirty") is False
        one.wait_for_function("!document.querySelector('#saveRois').disabled")
        saved = srv.project.load("rois.json", {})[two.evaluate("st.code")]
        assert saved == two.evaluate("st.rois") and saved != one.evaluate("st.rois")
    finally:
        go.set()
        srv.shutdown()
        srv.server_close()


WARNING_BOX = """() => {
  const msg = document.querySelector('#roiMsg'), text = msg.lastElementChild, cs = getComputedStyle(text);
  const box = el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; };
  const lh = parseFloat(cs.lineHeight) || 1.4 * parseFloat(cs.fontSize);
  return {lines: Math.round(text.getBoundingClientRect().height / lh), msg: box(msg), panel: box(document.querySelector('#panel')),
          button: box(document.querySelector('#saveRois')), icon: box(msg.querySelector('svg')),
          sr: msg.querySelector('.sr').textContent, said: text.textContent, role: msg.getAttribute('role'),
          color: cs.color, text: getComputedStyle(document.querySelector('#codeTitle')).color, scroll: [document.querySelector('#panel').scrollWidth, document.querySelector('#panel').clientWidth]};
}"""


def test_two_windows_that_save_outlines_one_after_the_other_both_say_so(playwright, fresh_project):
    """One window saved an image's outlines, then another window (which had the image open before that)
    saved its own. The later save is the saved one, as before, and neither window said anything: the first
    still read "Saved 1 outline". Now the second says it replaced outlines saved elsewhere, and the first
    says so when it is looked at again. Chromium, a 1920 x 1080 laptop at 125%."""
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"chromium is not installed for Playwright ({type(exc).__name__})")
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    context = browser.new_context(viewport={"width": 1536, "height": 785}, device_scale_factor=1.25)

    def open_page(_, url):
        page = context.new_page()
        page.goto(url)
        page.wait_for_function("document.querySelector('#codeTitle').textContent.startsWith('W')")
        return page
    try:
        one = open_page(context, url)
        one.evaluate("api(`/api/worm/${st.code}/score`, {usable: 'no', unusable_reason: 'x', flag_answers: {}})"
                     ".then(() => openWorm(st.code))")
        one.wait_for_function("document.querySelector('#markTab').getAttribute('aria-disabled') !== 'true'")
        two = open_page(context, url)
        two.evaluate("code => openWorm(code)", one.evaluate("st.code"))
        two.wait_for_function("document.querySelector('#markTab').getAttribute('aria-disabled') !== 'true'")
        code = one.evaluate("st.code")
        assert code == two.evaluate("st.code")
        for page, nth in ((one, 0), (two, 1)):
            page.locator("#markTab").click()
            page.locator("#sugList button").nth(nth).click()

        one.locator("#saveRois").click()
        one.wait_for_function("document.querySelector('#roiMsg').textContent === 'Saved 1 outline'")     # one window: as before
        assert one.evaluate("document.querySelector('.tab[data-tab=mark] .actions').classList.contains('warned')") is False
        one.evaluate("checkIn()")                                    # looked at again: its own outlines are the saved ones
        one.wait_for_timeout(300)
        assert one.locator("#roiMsg").inner_text() == "Saved 1 outline"

        two.locator("#saveRois").click()                             # the later save wins and says what it replaced
        two.wait_for_function("document.querySelector('#roiMsg').classList.contains('warn')")
        w = two.evaluate(WARNING_BOX)
        assert w["said"] == "Saved 1 outline, replacing those from another window. Use one scoring window at a time."
        assert srv.project.load("rois.json", {})[code] == two.evaluate("st.rois") != one.evaluate("st.rois")
        assert two.evaluate("st.roisDirty") is False

        one.evaluate("window.dispatchEvent(new Event('focus'))")     # the first window is looked at again
        one.wait_for_function("document.querySelector('#roiMsg').classList.contains('warn')")
        v = one.evaluate(WARNING_BOX)
        assert v["said"] == "Another window changed this image's outlines. Open the image again to see them, or save yours."
        for box in (w, v):
            assert box["sr"] == "Warning: " and box["role"] == "status"
            assert box["lines"] <= 2                                 # at most two lines
            assert box["icon"]["right"] > box["icon"]["left"] and box["icon"]["left"] >= box["msg"]["left"]
            assert box["panel"]["left"] <= box["msg"]["left"] and box["msg"]["right"] <= box["panel"]["right"]
            assert box["msg"]["bottom"] <= box["button"]["top"] + 1  # on its own row, above the button
            assert box["button"]["bottom"] <= 785 and box["scroll"][0] <= box["scroll"][1]
            assert box["color"] == box["text"]                        # the page's text colour, the icon in the warning colour
        # said once: drawing after the warning is not written over by the next check
        one.locator("#sugList button").last.click()
        assert one.locator("#roiMsg").inner_text() == "Unsaved changes"
        one.evaluate("checkIn()")
        one.wait_for_timeout(300)
        assert one.locator("#roiMsg").inner_text() == "Unsaved changes"
        # opening the image again shows the saved outlines and clears the warning
        one.evaluate("st.roisDirty = false; openWorm(st.code)")
        one.wait_for_function("document.querySelector('#roiMsg').textContent === ''")
        assert one.evaluate("st.rois") == srv.project.load("rois.json", {})[code]
        assert one.evaluate("document.querySelector('.tab[data-tab=mark] .actions').classList.contains('warned')") is False
    finally:
        context.close()
        browser.close()
        srv.shutdown()
        srv.server_close()


def test_the_outline_warnings_keep_to_two_lines_in_a_narrow_window(playwright, fresh_project):
    """Below 1100 px the side panel is 320 px wide; the warning about another window's outlines wrapped to
    three lines there (independent review of 1.26.18). Warnings are at most two lines."""
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:          # the Mac check installs WebKit only
        pytest.skip(f"chromium is not installed for Playwright ({type(exc).__name__})")
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        page = browser.new_page(viewport={"width": 1024, "height": 700}, device_scale_factor=1.25)
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
        page.wait_for_function("document.querySelector('#codeTitle').textContent.startsWith('W')")
        page.evaluate("api(`/api/worm/${st.code}/score`, {usable: 'no', unusable_reason: 'x', flag_answers: {}})"
                      ".then(() => openWorm(st.code))")
        page.wait_for_function("document.querySelector('#markTab').getAttribute('aria-disabled') !== 'true'")
        page.locator("#markTab").click()
        for text in ("Another window changed this image's outlines. Open the image again to see them, or save yours.",
                     "Saved 12 outlines, replacing those from another window. Use one scoring window at a time."):
            page.evaluate("t => roiMessage(t, 'warn')", text)
            assert page.evaluate(WARNING_BOX)["lines"] <= 2, text
    finally:
        browser.close()
        srv.shutdown()
        srv.server_close()


ANSWER_UNUSABLE = """reason => {
  const f = document.querySelector('#scoreForm');
  f.querySelector('input[name=usable][value=no]').click();
  const sel = document.querySelector('#reasonSelect');
  sel.value = [...sel.options].filter(o => o.value)[reason].value;
  sel.dispatchEvent(new Event('change', {bubbles: true}));
  for (const fl of st.meta.flags) f.querySelector(`input[name="flag_${fl.id}"]`)?.click();
}"""
SAVE_WARNING = WARNING_BOX.replace("#roiMsg", "#saveMsg").replace("#saveRois", "#scoreForm button[type=submit]")


def test_two_windows_that_save_a_score_one_after_the_other_both_say_so(playwright, fresh_project):
    """As for outlines: one window saved an image's score, then another window that had the image open
    before that saved its own. The later save is the saved one, as before, and neither window said anything.
    Now the second says it replaced a score saved elsewhere, and the first says so when it is looked at again."""
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"chromium is not installed for Playwright ({type(exc).__name__})")
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    context = browser.new_context(viewport={"width": 1536, "height": 785}, device_scale_factor=1.25)

    def opened():
        page = context.new_page()
        page.goto(url)
        page.wait_for_function("document.querySelector('#codeTitle').textContent.startsWith('W') && !st.loading")
        return page
    try:
        one, two = opened(), opened()
        code = one.evaluate("st.code")
        assert two.evaluate("st.code") == code
        save = lambda page: page.locator("#scoreForm button[type=submit]").click()
        one.evaluate(ANSWER_UNUSABLE, 0)
        save(one)
        one.wait_for_function("document.querySelector('#saveMsg').textContent.startsWith('Saved ')")
        assert not one.evaluate("document.querySelector('#saveMsg').classList.contains('warn')")    # one window: as before
        one.evaluate("checkIn()")                                    # looked at again: its own score is the saved one
        one.wait_for_timeout(300)
        assert not one.evaluate("document.querySelector('#saveMsg').classList.contains('warn')")

        two.evaluate(ANSWER_UNUSABLE, 1)
        save(two)                                                    # the later save wins and says what it replaced
        two.wait_for_function("document.querySelector('#saveMsg').classList.contains('warn')")
        w = two.evaluate(SAVE_WARNING)
        saved = srv.project.load("scores.json", {})[code]
        assert w["said"] == "Score saved, replacing one from another window. Use one scoring window at a time."
        assert saved["unusable_reason"] == two.evaluate("document.querySelector('#reasonSelect').value") \
            != one.evaluate("document.querySelector('#reasonSelect').value")
        assert two.evaluate("st.code") == code and two.evaluate("st.formDirty") is False

        one.evaluate("window.dispatchEvent(new Event('focus'))")     # the first window is looked at again
        one.wait_for_function("document.querySelector('#saveMsg').classList.contains('warn')")
        v = one.evaluate(SAVE_WARNING)
        assert v["said"] == "Another window changed this image's score. Open the image again to see it, or save yours."
        for box in (w, v):
            assert box["sr"] == "Warning: " and box["role"] == "status" and box["lines"] <= 2
            assert box["icon"]["right"] > box["icon"]["left"] >= box["msg"]["left"]
            assert box["panel"]["left"] <= box["msg"]["left"] and box["msg"]["right"] <= box["panel"]["right"]
            assert box["msg"]["bottom"] <= box["button"]["top"] + 1 and box["button"]["bottom"] <= 785
            assert box["scroll"][0] <= box["scroll"][1] and box["color"] == box["text"]
        # said once, and opening the image again shows the saved score and clears the warning
        one.evaluate("checkIn()")
        one.wait_for_timeout(300)
        assert one.evaluate(SAVE_WARNING)["said"] == v["said"]
        one.evaluate("openWorm(st.code)")
        one.wait_for_function("document.querySelector('#saveMsg').textContent.startsWith('Saved ')")
        assert one.evaluate("document.querySelector('#reasonSelect').value") == saved["unusable_reason"]
        assert not one.evaluate("document.querySelector('#scoreForm .actions').classList.contains('warned')")
    finally:
        context.close()
        browser.close()
        srv.shutdown()
        srv.server_close()


def test_the_score_warnings_keep_to_two_lines_in_a_narrow_window(playwright, fresh_project):
    """Below 1100 px the side panel is 320 px wide: both score warnings still take at most two lines."""
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:
        pytest.skip(f"chromium is not installed for Playwright ({type(exc).__name__})")
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    context = browser.new_context(viewport={"width": 1024, "height": 700}, device_scale_factor=1.25)
    try:
        pages = []
        for _ in range(2):
            page = context.new_page()
            page.goto(url)
            page.wait_for_function("document.querySelector('#codeTitle').textContent.startsWith('W') && !st.loading")
            pages.append(page)
        one, two = pages
        for page, reason in ((one, 0), (two, 1)):
            page.evaluate(ANSWER_UNUSABLE, reason)
            page.locator("#scoreForm button[type=submit]").click()
            page.wait_for_function("document.querySelector('#saveMsg').textContent.startsWith('Saved ') "
                                   "|| document.querySelector('#saveMsg').classList.contains('warn')")
        one.evaluate("window.dispatchEvent(new Event('focus'))")
        one.wait_for_function("document.querySelector('#saveMsg').classList.contains('warn')")
        for page in (one, two):
            box = page.evaluate(SAVE_WARNING)
            assert box["sr"] == "Warning: " and box["lines"] <= 2, box["said"]
            assert box["panel"]["left"] <= box["msg"]["left"] and box["msg"]["right"] <= box["panel"]["right"]
            assert box["scroll"][0] <= box["scroll"][1]
    finally:
        context.close()
        browser.close()
        srv.shutdown()
        srv.server_close()


ANSWER_USABLE = """() => {
  const f = document.querySelector('#scoreForm');
  for (const fl of st.meta.flags) f.querySelector(`input[name="flag_${fl.id}"]`)?.click();
  f.querySelector('input[name=usable][value=yes]').click();
  const names = new Set([...f.querySelectorAll('input[type=radio]')].map(i => i.name));
  for (const n of names) {
    if (n === 'usable' || n.startsWith('flag_') || f.querySelector(`input[name="${n}"]:checked`)) continue;
    [...f.querySelectorAll(`input[type=radio][name="${n}"]`)].find(i => !i.disabled)?.click();
  }
}"""


def test_the_score_warnings_show_for_a_usable_image(playwright, fresh_project):
    """After saving a usable image the page moves to Outline cells, which hid the Score tab and the warning
    in it: neither window's score warning could be seen in the usual case (second review of 1.26.18). The
    window that replaced a score now stays on the Score tab, and the other returns to it to say so."""
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:          # the Mac check installs WebKit only
        pytest.skip(f"chromium is not installed for Playwright ({type(exc).__name__})")
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    context = browser.new_context(viewport={"width": 1536, "height": 785}, device_scale_factor=1.25)

    def opened():
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
        page.wait_for_function("document.querySelector('#codeTitle').textContent.startsWith('W') && !st.loading")
        return page
    try:
        one, two = opened(), opened()
        code = one.evaluate("st.code")
        one.evaluate(ANSWER_USABLE)
        one.locator("#scoreForm button[type=submit]").click()
        one.wait_for_function("st.tab === 'mark'")                   # one window: on to the outlines, as before
        two.evaluate(ANSWER_USABLE)
        two.locator("label", has_text="Unsure").click()              # a different score from window one's
        two.locator("#scoreForm button[type=submit]").click()
        two.wait_for_function("document.querySelector('#saveMsg').classList.contains('warn')")
        assert two.evaluate("st.tab") == "score" and two.evaluate("st.code") == code
        assert two.locator("#saveMsg").is_visible()
        one.evaluate("window.dispatchEvent(new Event('focus'))")
        one.wait_for_function("document.querySelector('#saveMsg').classList.contains('warn')")
        assert one.evaluate("st.tab") == "score" and one.locator("#saveMsg").is_visible()
        assert one.evaluate(SAVE_WARNING)["said"] == \
            "Another window changed this image's score. Open the image again to see it, or save yours."
    finally:
        context.close()
        browser.close()
        srv.shutdown()
        srv.server_close()


def test_a_replaced_score_is_said_when_an_answer_changed_while_it_saved(playwright, fresh_project):
    """A save that replaced another window's score, during which an answer was changed, said only "you
    changed an answer while it saved", and the next save did not mention the replaced score."""
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:          # the Mac check installs WebKit only
        pytest.skip(f"chromium is not installed for Playwright ({type(exc).__name__})")
    srv = make_server(fresh_project, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    context = browser.new_context(viewport={"width": 1024, "height": 700}, device_scale_factor=1.25)
    try:
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
        page.wait_for_function("document.querySelector('#codeTitle').textContent.startsWith('W') && !st.loading")
        code = page.evaluate("st.code")
        import json as _json
        import urllib.request as _ur
        _ur.urlopen(_ur.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/worm/{code}/score",      # another window saves first
                                data=_json.dumps({"usable": "no", "unusable_reason": "x", "flag_answers": {}}).encode(),
                                headers={"Content-Type": "application/json"}), timeout=30).read()
        page.evaluate(ANSWER_UNUSABLE, 0)
        held = []
        page.route("**/score", lambda route: held.append(route))
        page.locator("#scoreForm button[type=submit]").click()
        page.wait_for_function("st.saving === true")
        page.locator("label", has_text="Fairly sure").click()                     # an answer changes meanwhile
        page.wait_for_timeout(100)
        held[0].continue_()
        page.wait_for_function("document.querySelector('#saveMsg').classList.contains('warn')")
        box = page.evaluate(SAVE_WARNING)
        assert box["said"] == "Score saved, replacing one from another window. Click Save again for your latest change."
        assert box["lines"] <= 2
    finally:
        context.close()
        browser.close()
        srv.shutdown()
        srv.server_close()


def test_the_image_fits_again_when_its_area_shrinks_without_a_window_resize(browser, url):
    # 1.26.26: the image re-fitted on window resize only; a warning shown above it left it too tall
    page = open_page(browser, url)
    page.wait_for_function("st.meta && st.drawn")
    page.evaluate("document.querySelector('#readbackWarn').classList.remove('hidden')")
    page.wait_for_function("parseFloat(cv.style.height) <= document.querySelector('#canvasWrap').clientHeight")
    # and nothing keeps redrawing while the page sits still (no resize loop)
    page.wait_for_timeout(200)
    resets = page.evaluate("""() => new Promise(done => {
        let k = 0; const fill = ctx.fillRect.bind(ctx);
        ctx.fillRect = (...a) => { k++; fill(...a); };
        setTimeout(() => { delete ctx.fillRect; done(k); }, 500); })""")
    assert resets == 0


@pytest.mark.parametrize("width", [1536, 900, 420])
def test_the_keys_line_keeps_its_height_when_the_depth_tip_shows(browser, url, width):
    # 1.26.26: the keys wrap at narrow widths and the one-line tip replaced them, so the image moved
    # while the cursor crossed it
    page = open_page(browser, url)
    page.set_viewport_size({"width": width, "height": 785})
    page.wait_for_timeout(100)
    row = "document.querySelector('.hint').getBoundingClientRect().height"
    keys = page.evaluate(row)
    page.evaluate("showDepth(3)")
    assert page.locator("#depthTip").is_visible()
    assert page.evaluate(row) == keys
    page.evaluate("showDepth(0)")
    assert page.evaluate(row) == keys


def test_score_and_outline_are_pressed_buttons_and_the_arrow_keys_still_move_planes(browser, url):
    # 1.26.26: they were role=tab without the rest of the tab pattern; a real tab list would take
    # the arrow keys, which move through the planes
    page = open_page(browser, url)
    page.wait_for_function("st.meta && st.drawn")
    buttons = page.locator(".tabs button")
    assert page.locator("[role=tab], [role=tablist]").count() == 0
    assert buttons.evaluate_all("bs => bs.map(b => [b.getAttribute('aria-pressed'), !!document.getElementById(b.getAttribute('aria-controls'))])") \
        == [["true", True], ["false", True]]
    page.evaluate("setTab('mark')")
    assert buttons.evaluate_all("bs => bs.map(b => b.getAttribute('aria-pressed'))") == ["false", "true"]
    page.evaluate("setTab('score')")
    if page.evaluate("st.meta.n_z") > 1:
        page.evaluate("setView('plane')")            # the arrow keys move through planes in the plane view
        z = page.evaluate("st.z")
        buttons.first.focus()
        page.keyboard.press("ArrowRight" if z == 0 else "ArrowLeft")
        page.wait_for_function(f"st.z !== {z}")
