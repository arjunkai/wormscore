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


def test_projects_on_the_home_screen_are_rows_with_a_folder_icon():
    """1.12.1: the project list reads like other apps' recent-projects lists (a folder icon, the
    name and the status, a fill for hover and for the open project), not a stack of boxes; it
    scrolls on its own."""
    css = (STATIC / "home.css").read_text(encoding="utf-8")
    js = (STATIC / "home.js").read_text(encoding="utf-8")
    pick = re.search(r"\.card-p \.pick \{([^}]*)\}", css).group(1)
    assert "border: 0" in pick
    assert 'icon("folder", 16)' in js and "folder:" in js
    assert re.search(r"\.card-p\.cur \.pick \{[^}]*background: var\(--accent-soft\)", css)
    assert re.search(r"\.recent \{[^}]*overflow-y: auto", css)


def test_project_settings_cards_match_the_criteria_cards():
    """1.12.3: Project settings cards use the panel colour and stronger edge of the Scoring criteria
    cards, so the darker typing boxes stand out (they matched the card before)."""
    css = (STATIC / "home.css").read_text(encoding="utf-8")
    ps = re.search(r"\.ps \.s-card\.src \{([^}]*)\}", css).group(1)
    sf = re.search(r"\.sf-card \{([^}]*)\}", css).group(1)
    for part in ("background: var(--panel)", "border-color: var(--line-strong)", "border-radius: var(--radius-lg)"):
        assert part in ps and part in sf, part


def _tokens(css, selector):
    block = re.search(re.escape(selector) + r" \{([^}]*)\}", css).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def _contrast(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_light_mode_edges_are_as_clear_as_dark_modes():
    """The owner found the light home page faded (1.26.18): card edges were 1.25:1 against the page in
    light mode against 2.06:1 in dark, and typing boxes 1.36:1. Input edges now meet WCAG 1.4.11 (3:1)
    in both themes; card edges are about as clear in light as in dark; and cards, optional cards,
    buttons and step circles use the stronger edge."""
    css = (STATIC / "home.css").read_text(encoding="utf-8")
    dark, light = _tokens(css, ':root, [data-theme="dark"]'), _tokens(css, '[data-theme="light"]')
    for t in (dark, light):
        assert _contrast(t["--line-input"], t["--panel"]) >= 3 and _contrast(t["--line-input"], t["--bg"]) >= 3
    assert _contrast(light["--line-strong"], light["--bg"]) >= 0.95 * _contrast(dark["--line-strong"], dark["--bg"])
    assert _contrast(light["--line"], light["--panel"]) >= _contrast(dark["--line"], dark["--panel"])
    for sel, prop in ((".step", "border: 1px solid var(--line-strong)"), (".extra", "border: 1px dashed var(--line-strong)"),
                      (".btn", "border: 1px solid var(--line-strong)"), (".num", "border: 2px solid var(--line-strong)"),
                      (".f-input", "border: 1px solid var(--line-input)")):
        assert prop in re.search(r"(?m)^" + re.escape(sel) + r" \{([^}]*)\}", css).group(1), sel
