"""Record the one-minute screen recording on wormscore.org: a project from Scan data to Unblind.

    python tools/demo_video.py OUT_DIR

Makes the real-worm demo project in OUT_DIR (4 heads from Sun and Hobert 2021, downloaded once and cached),
runs the app's home server in this process with its own settings file (your settings and recent projects
are not touched), drives it in Chromium with a drawn cursor, and writes OUT_DIR/demo.mp4 (1920x1080, about
a minute, no sound), demo-poster.webp (its first frame) and the raw frames. Waiting and images 2 to 4 are
sped up 3x. Every answer is "normal" / "yes": all four are normal animals.

Needs: pip install playwright pillow; python -m playwright install chromium; ffmpeg on PATH.
The scoring choices follow the demo's criteria; if the scoring form changes, adjust ANSWERS.
"""
import base64
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

W, H, DSF = 1280, 720, 1.5               # browser window; frames are 1920x1080
FAST = 3.0                               # speed-up while the app works and for images 2-4
ANSWERS = [("usable", "yes"), ("cells_seen", "2"), ("n1_call", "normal"), ("n1_process", "yes"),
           ("n2_call", "normal"), ("n2_process", "yes"), ("confidence", "sure")]
CAPTIONS = {1: "Scan data: find every image and read its metadata",
            2: "Set the scoring criteria, or start from a template",
            3: "Build the blind set: random codes, key sealed",
            4: "Score each coded image: no file names, no genotypes",
            5: "Unblind: results, tests and a draft Methods paragraph"}
INTRO, OUTRO = "wormscore: a project from start to finish", "Free and open source  ·  wormscore.org"

# a drawn cursor and a ring on each click: headless screenshots show no mouse
CURSOR = """
(() => {
  const css = `#rc-cur{position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;
     transform:translate(-3px,-2px)} #rc-cur svg{filter:drop-shadow(0 1px 2px rgba(0,0,0,.45))}
   .rc-ring{position:fixed;z-index:2147483646;pointer-events:none;width:34px;height:34px;margin:-17px 0 0 -17px;
     border-radius:50%;border:3px solid rgba(26,127,75,.85);animation:rcring .45s ease-out forwards}
   @keyframes rcring{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}`;
  const add = () => {
    if (document.getElementById('rc-cur')) return;
    const s = document.createElement('style'); s.textContent = css; document.head.appendChild(s);
    const c = document.createElement('div'); c.id = 'rc-cur';
    c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2 L3 18 L7.5 14 L10.5 20.5 L13.2 19.3 L10.3 13 L16.5 13 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    c.style.left = 'X0px'; c.style.top = 'Y0px'; document.body.appendChild(c);
  };
  document.addEventListener('mousemove', e => { const c = document.getElementById('rc-cur');
    if (c) { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; } }, true);
  document.addEventListener('mousedown', e => { const r = document.createElement('div'); r.className = 'rc-ring';
    r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px'; document.body.appendChild(r);
    setTimeout(() => r.remove(), 500); }, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
""".replace("X0", str(W // 2)).replace("Y0", str(H // 2))

READY = "typeof st !== 'undefined' && st.code && st.mip && !st.loading"     # scoring page has drawn an image


def start_home(out):
    from wormscore import home
    home.SETTINGS = out / "settings.json"
    app = home.HomeApp(open_windows=False, pick=lambda title: None, opener=lambda url: None)
    srv = home.make_home_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


def record(out, project):
    import urllib.request
    url, srv = start_home(out)

    def post(route, body):
        req = urllib.request.Request(f"{url}/api/{route}", json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)

    post("select", {"path": str(project)})
    post("theme", {"theme": "light"})
    frames_dir = out / "frames"
    frames_dir.mkdir(exist_ok=True)
    frames, caps, speed = [], [], [1.0]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=DSF)
        ctx.add_init_script(CURSOR)
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)

        def on_frame(ev):
            f = frames_dir / f"{len(frames):05d}.jpg"
            f.write_bytes(base64.b64decode(ev["data"]))
            frames.append([f.name, ev["metadata"]["timestamp"], speed[0]])
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        wait = page.wait_for_timeout

        def caption(n):
            caps.append([time.time(), n])

        def move(target, steps=22):
            if isinstance(target, tuple):
                x, y = target
            else:
                b = target.bounding_box()
                x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
            page.mouse.move(x, y, steps=steps)

        def click(sel, pause=350, steps=22):
            t = page.locator(sel).first if isinstance(sel, str) else sel
            # jump, not smooth-scroll, to anything near an edge, so the aim is taken where it ends up
            if t.evaluate("e => { const r = e.getBoundingClientRect(); if (r.top < 70 || r.bottom > innerHeight - 50) {"
                          " e.scrollIntoView({block: 'center', behavior: 'instant'}); return true } return false }"):
                wait(250)
            move(t, steps)
            wait(120)
            page.mouse.down()
            page.mouse.up()
            wait(pause)

        page.goto(url)
        page.wait_for_selector("#main:not(:has(.loading))")
        wait(500)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": int(W * DSF),
                                          "maxHeight": int(H * DSF), "everyNthFrame": 1})
        wait(1200)

        caption(1)
        wait(1300)
        click("button[data-k=step1]")
        speed[0] = FAST
        page.wait_for_selector("button:has-text('Scan again')", timeout=60000)
        speed[0] = 1.0
        wait(1400)

        caption(2)
        wait(800)
        click("button:has-text('Set criteria...')", pause=1300)
        click("button:has-text('Start from a template')", pause=2200)
        page.mouse.wheel(0, 300)
        wait(900)
        page.keyboard.press("Escape")
        wait(500)
        click("button:text-is('Save')", pause=1200)

        caption(3)
        wait(900)
        click("button:text-is('Build blind set')", pause=1200)
        click("#modal button.primary", pause=300)
        speed[0] = FAST
        page.wait_for_selector("button:has-text('Rebuild')", timeout=300000)
        speed[0] = 1.0
        wait(1500)

        caption(4)
        wait(700)
        click("button:has-text('Start scoring')", pause=200)
        page.goto(post("score", {})["url"])     # the app opens it in its own window; here the same tab
        page.wait_for_function(READY, timeout=60000)
        move((W * 0.4, H * 0.55))
        wait(900)
        click("button:text-is('Planes')", pause=500)
        bb = page.locator("#zSlider").bounding_box()
        y = bb["y"] + bb["height"] / 2
        move((bb["x"] + bb["width"] * 0.5, y))
        page.mouse.down()
        for f in [0.5, 0.35, 0.2, 0.3, 0.45, 0.6, 0.75, 0.6, 0.5]:
            page.mouse.move(bb["x"] + bb["width"] * f, y, steps=8)
            wait(140)
        page.mouse.up()
        wait(500)
        click("button:text-is('Projection')", pause=700)
        n = int(page.evaluate(r"() => document.body.innerText.match(/\d+ of (\d+) scored/)[1]"))
        for i in range(n):
            fast = i > 0
            steps = 10 if fast else 20
            if fast:
                speed[0] = FAST
            page.wait_for_function(READY, timeout=60000)
            wait(300)
            notes = page.locator("input[name^=flag_]:visible")     # "I have read this" on a warning
            for k in range(notes.count()):
                click(notes.nth(k), pause=150, steps=steps)
            for name, value in ANSWERS:
                loc = page.locator(f"input[name={name}][value='{value}']")
                if loc.count() and loc.first.is_visible():
                    click(loc.first, pause=150 if fast else 260, steps=steps)
            old = page.evaluate("st.code")
            click("button:text-is('Save score')", pause=500, steps=steps)
            if i < n - 1:
                wait(400)
                click("#nextBtn", pause=200, steps=steps)
                page.wait_for_function("c => st.code !== c", arg=old, timeout=30000)
            wait(900)
        speed[0] = 1.0
        wait(1200)

        page.goto(url)
        page.wait_for_selector("#main:not(:has(.loading))")
        caption(5)
        wait(1000)
        click("text=I have finished scoring", pause=500)
        click("button:text-is('Unblind')", pause=900)
        if page.locator("#modal:not([hidden]) button.primary").count():
            click("#modal:not([hidden]) button.primary", pause=300)
        speed[0] = FAST
        page.wait_for_selector("text=Last step: Unblind", timeout=300000)
        page.wait_for_function("() => !document.querySelector('[data-k=step1]')?.disabled", timeout=300000)
        speed[0] = 1.0
        wait(800)
        res = page.locator("[class*=result]").first
        if res.count():
            res.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")
        wait(4000)
        cdp.send("Page.stopScreencast")
        browser.close()
    srv.shutdown()
    if not (project / "outputs" / "unblinded" / "methods_text.md").is_file():
        sys.exit("Unblind did not finish; see the frames in " + str(frames_dir))
    return frames, caps


def make_video(out, frames, caps, fps=30, end_hold=3.0):
    cw, ch = 1920, 1080
    aw, ah = 1680, 945                    # the app, scaled, on a dark background with a caption below
    ax, ay = (cw - aw) // 2, 26
    font = ImageFont.truetype("C:/Windows/Fonts/seguisb.ttf" if sys.platform == "win32" else "DejaVuSans-Bold.ttf", 38)
    num_font = font.font_variant(size=28)
    out_t, t = [0.0], 0.0                 # output time of each frame, with the marked stretches sped up
    for (_, t0, s0), (_, t1, _) in zip(frames, frames[1:]):
        t += (t1 - t0) / s0
        out_t.append(t)
    mask = Image.new("L", (aw, ah), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, aw - 1, ah - 1], radius=14, fill=255)
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([ax, ay + 6, ax + aw, ay + ah + 6], radius=14, fill=(0, 0, 0, 150))
    base = Image.new("RGB", (cw, ch), (17, 20, 23))
    base.paste(shadow.filter(ImageFilter.GaussianBlur(14)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(14)))

    def compose(src, cap):
        im = base.copy()
        im.paste(Image.open(out / "frames" / src).convert("RGB").resize((aw, ah), Image.LANCZOS), (ax, ay), mask)
        d = ImageDraw.Draw(im)
        n, text = cap
        cy = ay + ah + (ch - ay - ah) // 2
        r = 22
        x = (cw - d.textlength(text, font=font) - (2 * r + 18 if n else 0)) / 2
        if n:
            d.ellipse([x, cy - r, x + 2 * r, cy + r], fill=(26, 127, 75))
            d.text((x + r, cy), str(n), font=num_font, fill="white", anchor="mm")
            x += 2 * r + 18
        d.text((x, cy), text, font=font, fill=(236, 239, 242), anchor="lm")
        return im

    def caption_at(src_time):
        cur = (0, INTRO)
        for ct, n in caps:
            if ct <= src_time:
                cur = (n, CAPTIONS[n])
        return cur

    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{cw}x{ch}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "slow",
                           "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / "demo.mp4")],
                          stdin=subprocess.PIPE)
    k, last_key, last = 0, None, None
    total = out_t[-1] + end_hold
    for i in range(int(total * fps)):
        ot = i / fps
        while k + 1 < len(frames) and out_t[k + 1] <= ot:
            k += 1
        cap = (0, OUTRO) if ot > out_t[-1] + 0.3 else caption_at(frames[k][1])
        if (frames[k][0], cap) != last_key:
            last_key, last = (frames[k][0], cap), compose(frames[k][0], cap)
            if i == int(0.5 * fps):
                last.resize((1280, 720), Image.LANCZOS).save(out / "demo-poster.webp", quality=82)
        ff.stdin.write(last.tobytes())
    ff.stdin.close()
    if ff.wait():
        sys.exit("ffmpeg failed")
    print(f"{out / 'demo.mp4'}: {total:.1f} s")


def main():
    out = Path(sys.argv[1]).resolve()
    project = out / "Projects" / "BAG demo"
    if project.exists():
        sys.exit(f"{project} exists: give an empty OUT_DIR")
    out.mkdir(parents=True, exist_ok=True)
    from wormscore.realdemo import make_real_demo
    make_real_demo(project, log=lambda *a: None)
    frames, caps = record(out, project)
    (out / "frames.json").write_text(json.dumps(frames))
    make_video(out, frames, caps)


if __name__ == "__main__":
    main()
