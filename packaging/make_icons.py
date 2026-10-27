"""Draw the wormscore logo and write every icon file from one drawing. Run from the repository
root:  python packaging/make_icons.py

The logo: a C. elegans crawling as it does on a plate (a sine wave, thickest mid-body, a pointed
tail and a blunt nose) with one labelled neuron lit in the head. Two shapes on the tile, so it
still reads at 16 px.

Outputs: src/wormscore/static/icon.svg (pages), packaging/wormscore.ico and
src/wormscore/static/wormscore.ico (Windows exe and window title bars, 16 to 256 px),
packaging/icon-1024.png (macOS app), src/wormscore/static/icon-192.png and icon-512.png,
packaging/social-preview.png (the card GitHub shows when the repository link is shared).
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "wormscore" / "static"
BIG = 2048                      # drawn large, then scaled down, for smooth edges
S = BIG / 32                    # everything is laid out on a 32 x 32 grid
TILE, BODY, CELL = "#121417", "#4fbf7a", "#d9fbe4"
NEURON_AT = 0.72                # along the body, tail = 0, nose = 1


def centre(t):
    """Centre line: tail at the lower left, nose at the right, one and a half waves. The bends are
    kept wider than the body (bend radius above half the width everywhere), so the inside of a
    bend stays round instead of folding into a sharp corner."""
    return 4.5 + 23 * t, 20.5 - 8.5 * t - 2.7 * math.sin(2 * math.pi * 1.5 * t + 0.3)


def half_width(t):
    """Thickest mid-body, narrowing to a point at the tail and to a blunt nose."""
    end = 0.3 + 0.75 * t
    return end + (1.75 - end) * math.sin(math.pi * min(t, 0.999)) ** 0.55


def discs(n):
    """The body as overlapping discs along the centre line (the union is a smooth tapered worm)."""
    return [(*centre(i / n), half_width(i / n)) for i in range(n + 1)]


def neuron():
    x, y = centre(NEURON_AT)
    return x, y, half_width(NEURON_AT) * 0.72      # inside the body, clear of its edge


def logo():
    im = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, BIG - 1, BIG - 1], radius=7 * S, fill=TILE)
    for x, y, r in discs(1600):
        d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=BODY)
    x, y, r = neuron()
    d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=CELL)
    return im


def svg():
    body = "".join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}"/>' for x, y, r in discs(220))
    x, y, r = neuron()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<rect width="32" height="32" rx="7" fill="{TILE}"/><g fill="{BODY}">{body}</g>'
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{CELL}"/></svg>\n')


def social_preview(big):
    """The 1280 x 640 card GitHub shows when the repository link is shared (uploaded by hand under
    Settings > Social preview). Segoe UI on Windows; Pillow's own font elsewhere."""
    fonts = Path("C:/Windows/Fonts")

    def font(name, size):
        try:
            return ImageFont.truetype(str(fonts / name), size)
        except OSError:
            return ImageFont.load_default(size)
    card = Image.new("RGB", (1280, 640), TILE)
    d = ImageDraw.Draw(card)
    name, url = font("seguisb.ttf", 100), font("segoeui.ttf", 26)
    # "Blind scoring of fluorescent neurons in C. elegans", the species name in italics
    tag = [("Blind scoring of fluorescent neurons in ", font("segoeui.ttf", 32)), ("C. elegans", font("segoeuii.ttf", 32))]
    text_w = max(d.textlength("wormscore", font=name), sum(d.textlength(t, font=f) for t, f in tag))
    logo_w = 260                        # the tile matches the card, so only the worm shows
    x0 = round((1280 - (logo_w + text_w)) / 2)
    logo_img = big.resize((logo_w, logo_w), Image.LANCZOS)
    card.paste(logo_img, (x0, 190), logo_img)
    tx = x0 + logo_w + 4
    d.text((tx, 196), "wormscore", font=name, fill="#e4e6e9")
    x = tx + 4
    for text, f in tag:
        d.text((x, 340), text, font=f, fill="#959ca6")
        x += d.textlength(text, font=f)
    d.text((tx + 4, 396), "github.com/arjunkai/wormscore", font=url, fill="#4fbf7a")
    card.save(ROOT / "packaging" / "social-preview.png", optimize=True)


def main():
    big = logo()
    sized = {n: big.resize((n, n), Image.LANCZOS) for n in (16, 20, 24, 32, 40, 48, 64, 128, 192, 256, 512, 1024)}
    ico = [(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    for path in (ROOT / "packaging" / "wormscore.ico", STATIC / "wormscore.ico"):
        sized[256].save(path, sizes=ico, append_images=[sized[n] for n in (16, 20, 24, 32, 40, 48, 64, 128)])
    sized[1024].save(ROOT / "packaging" / "icon-1024.png", optimize=True)
    for n in (192, 512):
        sized[n].save(STATIC / f"icon-{n}.png", optimize=True)
    (STATIC / "icon.svg").write_text(svg(), encoding="utf-8")
    social_preview(big)
    print("icons written")


if __name__ == "__main__":
    main()
