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
