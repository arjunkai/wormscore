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
