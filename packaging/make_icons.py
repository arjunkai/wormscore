"""Draw the wormscore logo (the same shape as src/wormscore/static/icon.svg) at high resolution
and write every icon size the apps need. Run from the repository root:  python packaging/make_icons.py

Outputs: packaging/wormscore.ico (Windows exe, 16 to 256 px), packaging/icon-1024.png (macOS app),
src/wormscore/static/icon-192.png and icon-512.png (taskbar and app-window icons).
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BIG = 2048                      # drawn large, then scaled down, for smooth edges
S = BIG / 32                    # the SVG is drawn on a 32 x 32 grid


def cubic(p0, p1, p2, p3, n=400):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / n for i in range(n + 1))]


def logo():
    im = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, BIG - 1, BIG - 1], radius=7 * S, fill="#121417")
    # path "M6 22 c4-10 8 2 12-6 s6-6 8-4", in absolute points
    pts = cubic((6, 22), (10, 12), (14, 24), (18, 16)) + cubic((18, 16), (22, 8), (24, 10), (26, 12))[1:]
    pts = [(x * S, y * S) for x, y in pts]
    r = 1.5 * S                                          # stroke width 3 on the 32 grid
    # A round-capped stroke drawn as overlapping discs along the curve: smooth edges at any size.
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        steps = max(1, int(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5 // 2))
        for k in range(steps + 1):
            x, y = x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps
            d.ellipse([x - r, y - r, x + r, y + r], fill="#4fbf7a")
    d.ellipse([15 * S, 13 * S, 21 * S, 19 * S], fill="#b8f5c9")
    return im


def main():
    big = logo()
    sized = {n: big.resize((n, n), Image.LANCZOS) for n in (16, 20, 24, 32, 40, 48, 64, 128, 192, 256, 512, 1024)}
    sized[256].save(ROOT / "packaging" / "wormscore.ico",
                    sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
                    append_images=[sized[n] for n in (16, 20, 24, 32, 40, 48, 64, 128)])
    sized[1024].save(ROOT / "packaging" / "icon-1024.png", optimize=True)
    for n in (192, 512):
        sized[n].save(ROOT / "src" / "wormscore" / "static" / f"icon-{n}.png", optimize=True)
    print("icons written")


if __name__ == "__main__":
    main()
