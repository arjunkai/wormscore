"""Update, then Go back, with the packaged app (Windows folder or macOS wormscore.app).

Builds the app, builds it again with a higher version number, zips the second build as a release
does, and installs it over the first through the app's own updater (`wormscore update --install`),
then goes back (`wormscore update --rollback`). After each step it checks which version
`wormscore --version` reports and that the app and the kept copy hold every file of their build.
It then repeats both steps with the app folder held (Windows: a file in it kept open, as a virus
scanner or a second copy of the app does; macOS: the app marked unchangeable) and checks that a
swap that fails leaves every copy whole and that Go back works afterwards. With --other-volume
(macOS) it does all of that a second time with the app on another volume than the home folder, where
the kept copy must be next to the app, so that no move of the exchange is a copy between volumes.

No release is involved: the updater reads the "latest release" from a local file
(WORMSCORE_UPDATE_FILE, see update.latest_url), and the app's data folder and home folder are
set to a scratch folder for the run, so the real settings and any real kept copy are not touched.

    python packaging/update_check.py                      builds both copies (several minutes)
    python packaging/update_check.py --first dist/wormscore.app      uses a build that exists (macOS)
    python packaging/update_check.py --first dist/wormscore          uses a build that exists (Windows)

Needs: pip install -e .[dev]. Run by .github/workflows/win-app.yml and mac-ui.yml.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC = sys.platform == "darwin"
WIN = sys.platform == "win32"
APP = "wormscore.app" if MAC else "wormscore"
ASSET = "wormscore-mac.zip" if MAC else "wormscore-windows.zip"


def say(text):
    print(text, flush=True)


def fail(text):
    say(f"FAILED: {text}")
    sys.exit(1)


def source_version(root):
    return re.search(r'__version__ = "([^"]+)"', (root / "src" / "wormscore" / "__init__.py").read_text(encoding="utf-8")).group(1)


def build(root, dist, work):
    """PyInstaller build of the tree at `root`; returns the app (folder, or wormscore.app on macOS)."""
    env = dict(os.environ, PYTHONPATH=str(root / "src"))       # this tree's wormscore, not an installed one
    subprocess.run([sys.executable, "-m", "PyInstaller", str(root / "packaging" / "wormscore.spec"), "--noconfirm",
                    "--distpath", str(dist), "--workpath", str(work)], check=True, env=env, cwd=str(root))
    return dist / APP


def higher_version_tree(dest, version):
    """A copy of the source with only the version number raised."""
    for name in ("src", "packaging"):
        shutil.copytree(ROOT / name, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    shutil.copy(ROOT / "LICENSE", dest / "LICENSE")
    init = dest / "src" / "wormscore" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    new = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text)
    if new == text:
        fail("could not set the higher version number")
    init.write_text(new, encoding="utf-8")
    return dest


def copy_app(src, dest):
    if MAC:
        subprocess.run(["ditto", str(src), str(dest)], check=True)      # keeps the bundle's links and permissions
    else:
        shutil.copytree(src, dest)


def release_zip(app, dist, out):
    """The zip as the release has it. Windows: packaging/build_windows.bat. macOS: build-mac.yml."""
    if MAC:
        stage = out / "stage" / "wormscore"
        stage.mkdir(parents=True)
        subprocess.run(["ditto", str(app), str(stage / "wormscore.app")], check=True)
        for name in ("LICENSE.txt", "THIRD_PARTY_LICENSES.txt"):
            shutil.copy(dist / "wormscore" / name, stage / name)
        shutil.copy(ROOT / "packaging" / "README-mac.txt", stage / "README.txt")
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(stage), str(out / ASSET)], check=True)
    else:
        shutil.make_archive(str(out / "wormscore-windows"), "zip", str(dist), "wormscore")
    return out / ASSET


def listing(app):
    """Every file and link in an app, by its path inside it (with sizes for files)."""
    out = {}
    for folder, dirs, files in os.walk(app):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(folder, d))]:
            p = Path(folder) / name
            out[p.relative_to(app).as_posix()] = "link" if p.is_symlink() else p.stat().st_size
    return out


class Check:
    def __init__(self, work, first, second, v1, v2, other_volume=None):
        """other_volume (macOS): a folder on another volume than `work`. The app is installed there and
        the home folder stays in `work`, as on a Mac whose app is on an external disk."""
        self.home = work / ("home-other-volume" if other_volume else "home")
        self.install = (Path(other_volume) / "Applications" if other_volume else work / "install") / APP
        self.v = {"first": v1, "second": v2}
        self.files = {"first": listing(first), "second": listing(second)}
        if MAC:
            self.data = self.home / "Library" / "Application Support" / "wormscore"
            self.previous = self.data / "previous" / APP
            if other_volume:
                # the kept copy is then next to the app, so that every move is a rename on its volume
                self.install.parent.mkdir(parents=True)
                self.home.mkdir(parents=True)
                if os.stat(self.install.parent).st_dev == os.stat(self.home).st_dev:
                    fail(f"{other_volume} is on the same volume as {work}, so this case would test nothing")
                self.previous = self.install.parent / ".wormscore-previous" / APP
            self.exe = self.install / "Contents" / "MacOS" / "wormscore"
            self.env = dict(os.environ, HOME=str(self.home))
        else:
            self.data = self.home / "AppData" / "Local" / "wormscore"
            self.previous = self.install.with_name(APP + "-previous")
            self.exe = self.install / "wormscore.exe"
            self.env = dict(os.environ, USERPROFILE=str(self.home), LOCALAPPDATA=str(self.home / "AppData" / "Local"))
        self.data.parent.mkdir(parents=True, exist_ok=True)
        self.env["WORMSCORE_UPDATE_FILE"] = str(work / "release" / "latest.json")
        self.env.pop("WORMSCORE_UPDATE_URL", None)
        copy_app(first, self.install)

    def clear_stage(self):
        """After a swap that failed, the new copy is still where it was unpacked. Remove it."""
        for name in (APP + "-update", ".wormscore-update"):
            if (self.install.parent / name).exists():
                shutil.rmtree(self.install.parent / name)

    def on_one_volume(self, what):
        """macOS, the app on another volume: nothing of the exchange may be in the data folder (a move
        from there would be a copy), and nothing half-made may be next to the app."""
        if not MAC or self.previous.parent.name != ".wormscore-previous":
            return
        for p in (self.data / "previous", self.data / "update-stage"):
            if p.exists():
                fail(f"{what}: {p} exists, on another volume than the app")
        if os.stat(self.previous).st_dev != os.stat(self.install).st_dev:
            fail(f"{what}: the kept copy is not on the app's volume")
        say(f"  OK: {what}: the kept copy is next to the app, on its volume")

    def run(self, *args):
        r = subprocess.run([str(self.exe), *args], capture_output=True, text=True, timeout=900, env=self.env,
                           cwd=str(self.home))
        out = (r.stdout + r.stderr).strip()
        say(f"  $ wormscore {' '.join(args)}\n    " + out.replace("\n", "\n    "))
        if r.returncode:
            fail(f"wormscore {' '.join(args)} ended with code {r.returncode}")
        return out

    def swap_result(self, timeout=420):
        """What the helper reported once the command had ended (it writes update-result.txt last)."""
        f = self.data / "update-result.txt"
        end = time.time() + timeout
        while not f.is_file():
            if time.time() > end:
                fail("the swap helper did not report within the time allowed")
            time.sleep(1)
        time.sleep(1)
        lines = f.read_text(encoding="utf-8-sig").splitlines()
        f.unlink()
        ok = bool(lines) and lines[0].strip() == "ok"
        say(f"    helper: {'ok' if ok else 'failed'} {' '.join(lines[1:]).strip()}")
        return ok

    def expect(self, what, current, kept):
        """The app reports version `current` and holds every file of that build; so does the kept copy."""
        got = self.run("--version")
        if got != f"wormscore {self.v[current]}":
            fail(f"{what}: the app reports {got!r}, expected wormscore {self.v[current]}")
        for name, folder, build in (("app", self.install, current), ("kept copy", self.previous, kept)):
            if build is None:
                if folder.exists():
                    fail(f"{what}: a kept copy exists and none was expected")
                continue
            have, want = listing(folder) if folder.exists() else {}, self.files[build]
            missing = sorted(set(want) - set(have))
            other = sorted(k for k in want if k in have and have[k] != want[k])
            extra = sorted(k for k in set(have) - set(want) if "__pycache__" not in k)
            if missing or other or extra:
                fail(f"{what}: the {name} is not the whole {build} build. Missing: {missing[:5]} ({len(missing)}); "
                     f"different: {other[:5]} ({len(other)}); extra: {extra[:5]} ({len(extra)})")
        if kept is not None:
            rec = json.loads((self.data / "previous.json").read_text(encoding="utf-8-sig"))["version"]
            if rec != self.v[kept]:
                fail(f"{what}: the kept copy is recorded as {rec}, and it is {self.v[kept]}")
        leftovers = [p.name for p in self.install.parent.iterdir() if p.name.endswith((".old", "-update"))]
        if leftovers:
            fail(f"{what}: left behind next to the app: {leftovers}")
        say(f"  OK: {what}: wormscore {self.v[current]}, whole"
            + (f"; kept copy {self.v[kept]}, whole" if kept else ""))

    def hold(self):
        """Stop the app folder from being moved. Returns the function that lets it go."""
        if MAC:
            subprocess.run(["chflags", "uchg", str(self.install)], check=True)
            return lambda: subprocess.run(["chflags", "nouchg", str(self.install)], check=True)
        inside = sorted(p for p in (self.install / "_internal").iterdir() if p.is_file())
        fh = open(inside[0] if inside else self.install / "LICENSE.txt", "rb")       # open in another program
        return fh.close


def sequence(c):
    """Update, Go back, both again with the app folder held, then Go back with it free."""
    c.expect("before the update", "first", None)

    say("Update")
    c.run("update", "--install")
    if not c.swap_result():
        fail("the update did not go through")
    c.expect("after the update", "second", "first")
    c.on_one_volume("after the update")

    say("Go back")
    c.run("update", "--rollback")
    if not c.swap_result():
        fail("Go back did not go through")
    c.expect("after Go back", "first", "second")

    say("Update with the app folder held: the swap must fail and change nothing")
    release_hold = c.hold()
    try:
        c.run("update", "--install")
        if c.swap_result():
            fail("the swap went through although the app folder was held, so this case tested nothing")
        c.clear_stage()
        c.expect("after the failed update", "first", "second")
        say("Go back with the app folder still held: it must fail and keep the kept copy")
        c.run("update", "--rollback")
        if c.swap_result():
            fail("Go back went through although the app folder was held, so this case tested nothing")
        c.clear_stage()
        c.expect("after the failed Go back", "first", "second")
    finally:
        release_hold()

    say("Go back once the app folder is free again")
    c.run("update", "--rollback")
    if not c.swap_result():
        fail("Go back did not go through after the failed update")
    c.expect("after Go back following the failed update", "second", "first")
    c.on_one_volume("after Go back following the failed update")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--first", help="an existing build of this source to use as the installed app")
    ap.add_argument("--work", help="scratch folder (default: a new temporary folder)")
    ap.add_argument("--other-volume", help="macOS: an empty folder on another volume; the checks are repeated with "
                    "the app installed there and the home folder on the first volume")
    a = ap.parse_args()
    if not (MAC or WIN):
        fail("the downloaded app exists for Windows and macOS only")
    if a.other_volume and not MAC:
        fail("--other-volume is for macOS: on Windows the copies are always next to the app folder")
    work = Path(a.work).resolve() if a.work else Path(tempfile.mkdtemp(prefix="wormscore-update-check-"))
    if work.exists() and any(work.iterdir()):
        fail(f"{work} is not empty")
    work.mkdir(parents=True, exist_ok=True)
    v1 = source_version(ROOT)
    major, minor, patch = (int(x) for x in v1.split("."))
    v2 = f"{major}.{minor}.{patch + 1}"

    if a.first:
        first, dist1 = Path(a.first).resolve(), Path(a.first).resolve().parent
    else:
        say(f"Building wormscore {v1}...")
        dist1 = work / "dist1"
        first = build(ROOT, dist1, work / "build1")
    say(f"Building wormscore {v2} (the same source with a higher version number)...")
    dist2 = work / "dist2"
    second = build(higher_version_tree(work / "tree2", v2), dist2, work / "build2")

    release = work / "release"
    release.mkdir()
    zip_path = release_zip(second, dist2, release)
    # what GitHub's "latest release" answer holds, as far as the updater reads it
    (release / "latest.json").write_text(json.dumps({
        "tag_name": f"v{v2}", "body": "Update check build.", "html_url": "",
        "assets": [{"name": ASSET, "browser_download_url": zip_path.as_uri(), "size": zip_path.stat().st_size,
                    "digest": "sha256:" + hashlib.sha256(zip_path.read_bytes()).hexdigest()}]}), encoding="utf-8")

    sequence(Check(work, first, second, v1, v2))
    if a.other_volume:
        say(f"The same with the app on another volume than the home folder ({a.other_volume})")
        sequence(Check(work, first, second, v1, v2, other_volume=a.other_volume))
    say("Update and Go back: all checks passed.")


if __name__ == "__main__":
    main()
