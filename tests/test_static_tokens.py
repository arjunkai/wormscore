"""The home screen and the scoring page share one set of design tokens. A token used on one page
but missing on the other makes the browser drop the whole rule silently, so the sets must match."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "wormscore" / "static"
# the scoring page is denser, so these two may differ on purpose
PAGE_SPECIFIC = {"--radius", "--radius-lg"}


def dark_tokens(name):
    """Custom properties set for the dark theme (the :root blocks) of one stylesheet."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / name).read_text(encoding="utf-8"), flags=re.S)
    out = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector.strip() in (":root", ':root, [data-theme="dark"]'):
            out.update(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))
    return {k: v.strip() for k, v in out.items()}


def test_both_pages_have_the_same_tokens():
    home, scoring = dark_tokens("home.css"), dark_tokens("style.css")
    assert home and scoring
    assert set(home) - set(scoring) == set(), "tokens missing on the scoring page"
    differ = {k for k in set(home) & set(scoring) - PAGE_SPECIFIC if home[k] != scoring[k]}
    assert differ == set(), f"tokens with different values: {sorted(differ)}"


def test_every_token_used_is_defined():
    for name in ("home.css", "style.css"):
        css = (STATIC / name).read_text(encoding="utf-8")
        defined = set(re.findall(r"(--[\w-]+)\s*:", css))
        used = set(re.findall(r"var\((--[\w-]+)", css))
        assert used - defined == set(), f"{name} uses undefined tokens"


def test_screens_use_one_word_for_each_thing():
    """The wording pass (1.12.0): what you draw around a cell is an outline everywhere (ROI only in
    the "?" help that defines it), and the criteria are no longer called a form on screen."""
    text = "\n".join((STATIC / n).read_text(encoding="utf-8") for n in ("index.html", "app.js", "home.js", "terms.js"))
    for old in ("Save ROIs", "Mark cells", "mark cells", "Saved ${st.rois.length} region", "Unknown starting form",
                "shown in Project settings will be used"):
        assert old not in text, old


def test_side_list_sits_on_the_border_line_on_both_pages():
    """A neuron's side list is centred on the fieldset's top border, clear of the first answers
    (it hung into them before 1.12.0). Same offset on the scoring page and in the criteria preview."""
    for name in ("style.css", "home.css"):
        css = (STATIC / name).read_text(encoding="utf-8")
        assert re.search(r"fieldset\.neuron > select\.side \{ position: absolute; top: -19px;", css), name
