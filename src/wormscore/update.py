"""In-app updates for the downloaded app (Windows folder or macOS wormscore.app).

check() asks GitHub for the latest release. Nothing about the user or their projects is
sent: one GET request with a "wormscore/<version>" User-Agent. install() downloads this
platform's zip, compares its SHA-256 with the checksum GitHub lists for it, unpacks it
next to the app and runs the new copy's --version. Only then does a small helper script
take over: it waits for this app to close, moves the current copy aside as the previous
version (kept for one-click rollback), moves the new copy into its place and starts it.
If any move fails it puts the old copy back. pip installs are told to use pip instead.

Updates never touch projects, codes, scores or settings files.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from . import __version__

LATEST_URL = "https://api.github.com/repos/arjunkai/wormscore/releases/latest"
RELEASES_PAGE = "https://github.com/arjunkai/wormscore/releases/latest"
PIP_COMMAND = "pip install -U wormscore"
ASSETS = {"windows": "wormscore-windows.zip", "mac": "wormscore-mac.zip"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(Exception):
    pass


def latest_url():
    # Another address only for testing an update against a local copy of the release.
    return os.environ.get("WORMSCORE_UPDATE_URL") or LATEST_URL


def parse_version(text):
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", str(text).strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(candidate, current=__version__):
    c, cur = parse_version(candidate), parse_version(current)
    return bool(c and cur and c > cur)


def data_dir():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    return base / "wormscore"


def install_kind():
    """'windows' or 'mac' for the downloaded app, 'pip' for a Python install."""
    if not getattr(sys, "frozen", False):
        return "pip"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin" and app_paths("mac")["current"].suffix == ".app":
        return "mac"
    return "other"


def app_paths(kind=None):
    """Where the app is, where the previous version is kept and where a new one is unpacked.
    Windows keeps them next to the app folder, so every move is a rename on one drive."""
    kind = kind or install_kind()
    if kind == "windows":
        cur = Path(sys.executable).resolve().parent
        return {"current": cur, "previous": cur.with_name(cur.name + "-previous"),
                "stage": cur.with_name(cur.name + "-update"), "app": "wormscore.exe"}
    if kind == "mac":
        cur = Path(sys.executable).resolve().parents[2]            # wormscore.app/Contents/MacOS/wormscore
        d = data_dir()
        return {"current": cur, "previous": d / "previous" / cur.name, "stage": d / "update-stage",
                "app": "wormscore.app"}
    return {}


def previous_version():
    """Version of the copy kept for rollback, or None."""
    p = app_paths()
    try:
        info = json.loads((data_dir() / "previous.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if p and p["previous"].exists() and parse_version(info.get("version", "")):
        return info["version"]
    return None


def last_result():
    """What the helper reported about the last update or rollback, read once."""
    f = data_dir() / "update-result.txt"
    try:
        lines = f.read_text(encoding="utf-8-sig").splitlines()
        f.unlink()
    except OSError:
        return None
    return {"ok": bool(lines) and lines[0].strip() == "ok", "message": " ".join(lines[1:]).strip()}


# ------------------------------------------------------------------ check

def check(timeout=5):
    """The latest release: {version, notes, page, asset}, asset being this platform's zip
    (None for pip installs or when the release has no zip for this platform)."""
    req = urllib.request.Request(latest_url(), headers={"User-Agent": f"wormscore/{__version__}",
                                                        "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"Could not reach GitHub: {exc}")
    version = str(rel.get("tag_name", "")).lstrip("v")
    if not parse_version(version):
        raise UpdateError("GitHub's latest release has no version number.")
    name = ASSETS.get(install_kind())
    asset = next(({"name": a["name"], "url": a["browser_download_url"], "size": a.get("size") or 0,
                   "digest": a.get("digest") or ""} for a in rel.get("assets", []) if a.get("name") == name), None)
    return {"version": version, "notes": clean_notes(rel.get("body") or ""),
            "page": rel.get("html_url") or RELEASES_PAGE, "asset": asset}


def clean_notes(md, limit=3000):
    """Release notes as plain text for the banner."""
    text = re.sub(r"^#+\s*", "", md.replace("\r\n", "\n"), flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n..."


# ------------------------------------------------------------------ install

def can_write(paths):
    parent = paths["current"].parent
    probe = parent / f".wormscore-write-test-{os.getpid()}"
    try:
        probe.mkdir()
        probe.rmdir()
        return True
    except OSError:
        return False


def download(asset, dest, progress=lambda done, total: None):
    """Download the zip and check it against the SHA-256 GitHub lists for it."""
    want = asset.get("digest", "")
    if not want.startswith("sha256:"):
        raise UpdateError("GitHub lists no checksum for this download, so it cannot be checked.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    h = hashlib.sha256()
    done = 0
    req = urllib.request.Request(asset["url"], headers={"User-Agent": f"wormscore/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as fh:
            total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
            while chunk := r.read(1 << 20):
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(done, total)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Download failed: {exc}")
    if h.hexdigest() != want.split(":", 1)[1].lower():
        part.unlink(missing_ok=True)
        raise UpdateError("The download does not match GitHub's checksum. Nothing was changed.")
    part.replace(dest)
    return dest


def unpack(zip_path, stage, kind):
    """Unpack into an empty stage folder and return the new app (folder or .app) inside it."""
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    if kind == "mac":
        # ditto keeps the app bundle's symlinks and permissions; zipfile would drop them.
        r = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(stage)], capture_output=True, text=True)
        if r.returncode:
            raise UpdateError(f"Could not unpack the download: {r.stderr.strip()}")
    else:
        root = stage.resolve()
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                target = (stage / name).resolve()
                if not target.is_relative_to(root):
                    raise UpdateError("The download contains a file outside its folder. Nothing was changed.")
            z.extractall(stage)
    want = "wormscore.app" if kind == "mac" else "wormscore.exe"
    found = [p for p in (stage / want, *stage.glob(f"*/{want}")) if p.exists()]
    if len(found) != 1:
        raise UpdateError(f"The download has no {want}. Nothing was changed.")
    return found[0] if kind == "mac" else found[0].parent


def new_version(app, kind):
    """Run the unpacked copy's --version: proves it starts and is the version expected."""
    exe = app / "Contents" / "MacOS" / "wormscore" if kind == "mac" else app / "wormscore.exe"
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=120,
                           creationflags=NO_WINDOW, cwd=str(Path.home()))
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"The new version does not start: {exc}")
    m = re.search(r"wormscore (\S+)", r.stdout)
    return m.group(1) if m else None


WINDOWS_HELPER = r"""param([int]$WaitPid, [string]$Current, [string]$New, [string]$Previous, [string]$Result, [string]$Launch)
# Written by wormscore: swaps in a new app folder after wormscore has closed.
$ErrorActionPreference = 'Stop'
function Move-Retry($from, $to) {
  for ($i = 0; ; $i++) {
    try { Move-Item -LiteralPath $from -Destination $to; return }
    catch { if ($i -ge 60) { throw }; Start-Sleep -Seconds 1 }
  }
}
try { Wait-Process -Id $WaitPid -Timeout 120 } catch {}
$old = "$Previous.old"
$status = @('ok', '')
try {
  if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
  if (Test-Path -LiteralPath $Previous) { Move-Retry $Previous $old }
  try { Move-Retry $Current $Previous }
  catch { if (Test-Path -LiteralPath $old) { Move-Item -LiteralPath $old -Destination $Previous }; throw }
  try { Move-Retry $New $Current }
  catch {
    Move-Item -LiteralPath $Previous -Destination $Current
    if (Test-Path -LiteralPath $old) { Move-Item -LiteralPath $old -Destination $Previous }
    throw
  }
  if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }
  $stage = Split-Path -Parent $New
  if (-not (Get-ChildItem -LiteralPath $stage -Force)) { Remove-Item -LiteralPath $stage -ErrorAction SilentlyContinue }
} catch {
  $status = @('failed', "$($_.Exception.Message) The previous version was kept.")
}
Set-Content -LiteralPath $Result -Value $status -Encoding UTF8
if ($Launch) { Start-Process -FilePath (Join-Path $Current $Launch) -WorkingDirectory $HOME }
"""

POSIX_HELPER = r"""#!/bin/sh
# Written by wormscore: swaps in a new app after wormscore has closed.
# Arguments: wait_pid current new previous result launch(1 or empty)
pid="$1"; current="$2"; new="$3"; previous="$4"; result="$5"; launch="$6"
i=0
while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 240 ]; do sleep 0.5; i=$((i+1)); done
retry() { n=0; until mv "$1" "$2" 2>/dev/null; do n=$((n+1)); [ "$n" -ge 60 ] && return 1; sleep 1; done; }
old="$previous.old"
status="ok"; msg=""
rm -rf "$old"
mkdir -p "$(dirname "$previous")"
if [ -e "$previous" ] && ! retry "$previous" "$old"; then
  status="failed"; msg="Could not move the previous copy aside."
elif ! retry "$current" "$previous"; then
  [ -e "$old" ] && mv "$old" "$previous"
  status="failed"; msg="Could not move the current copy aside."
elif ! retry "$new" "$current"; then
  mv "$previous" "$current"; [ -e "$old" ] && mv "$old" "$previous"
  status="failed"; msg="Could not move the new copy into place."
else
  rm -rf "$old"
  rmdir "$(dirname "$new")" 2>/dev/null
fi
[ "$status" = "failed" ] && msg="$msg The previous version was kept."
printf '%s\n%s\n' "$status" "$msg" > "$result"
[ -n "$launch" ] && open "$current"
exit 0
"""


def start_helper(paths, new, pid=None, relaunch=True):
    """Start the helper that swaps `new` into place once process `pid` (this app) has exited."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    result = d / "update-result.txt"
    result.unlink(missing_ok=True)
    pid = os.getpid() if pid is None else pid
    if sys.platform == "win32":
        script = d / "update-helper.ps1"
        script.write_text(WINDOWS_HELPER, encoding="utf-8")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
               "-WaitPid", str(pid), "-Current", str(paths["current"]), "-New", str(new),
               "-Previous", str(paths["previous"]), "-Result", str(result),
               "-Launch", paths["app"] if relaunch else ""]
        flags = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:   # leave any job object this app runs in, so closing the app does not stop the helper
            return subprocess.Popen(cmd, cwd=str(Path.home()), creationflags=flags | 0x01000000, close_fds=True)
        except OSError:
            return subprocess.Popen(cmd, cwd=str(Path.home()), creationflags=flags, close_fds=True)
    script = d / "update-helper.sh"
    script.write_text(POSIX_HELPER, encoding="utf-8")
    return subprocess.Popen(["/bin/sh", str(script), str(pid), str(paths["current"]), str(new),
                             str(paths["previous"]), str(result), "1" if relaunch else ""],
                            cwd=str(Path.home()), start_new_session=True, close_fds=True)


def _remember_previous(version):
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "previous.json").write_text(json.dumps({"version": version}), encoding="utf-8")


def prepare_install(info, say=lambda text: None, progress=lambda done, total: None):
    """Download, check and unpack the new version. Returns (paths, new app) for start_helper."""
    kind = install_kind()
    if kind not in ASSETS:
        raise UpdateError(f"This copy was installed with Python. Update it with:  {PIP_COMMAND}")
    if not info.get("asset"):
        raise UpdateError(f"The latest release has no {ASSETS[kind]}. Download it from {RELEASES_PAGE}")
    paths = app_paths(kind)
    if kind == "mac" and "/AppTranslocation/" in paths["current"].as_posix():
        # opened straight from the download: macOS runs it from a hidden read-only copy
        raise UpdateError("Move wormscore.app into your Applications folder (drag it there in Finder), "
                          "open it from there, then update.")
    if not can_write(paths):
        raise UpdateError(f"wormscore cannot write to {paths['current'].parent}. "
                          f"Download the new version from {RELEASES_PAGE}")
    say(f"Downloading wormscore {info['version']}...")
    zip_path = download(info["asset"], data_dir() / "updates" / info["asset"]["name"], progress)
    say("Download matches GitHub's checksum. Unpacking...")
    new = unpack(zip_path, paths["stage"], kind)
    got = new_version(new, kind)
    if got != info["version"]:
        shutil.rmtree(paths["stage"], ignore_errors=True)
        raise UpdateError(f"The new copy reports version {got}, expected {info['version']}. Nothing was changed.")
    zip_path.unlink(missing_ok=True)
    say(f"wormscore {got} is ready. It replaces this copy when the app closes.")
    return paths, new


def install(info, say=lambda text: None, progress=lambda done, total: None, relaunch=True):
    paths, new = prepare_install(info, say, progress)
    _remember_previous(__version__)
    start_helper(paths, new, relaunch=relaunch)


def rollback(relaunch=True):
    """Swap the kept previous version back in (this version becomes the previous one)."""
    kind = install_kind()
    back = previous_version()
    if kind not in ASSETS or not back:
        raise UpdateError("There is no previous version to go back to.")
    paths = app_paths(kind)
    if paths["stage"].exists():
        shutil.rmtree(paths["stage"])
    paths["stage"].mkdir(parents=True)
    new = paths["stage"] / paths["previous"].name
    shutil.move(str(paths["previous"]), str(new))
    _remember_previous(__version__)
    start_helper(paths, new, relaunch=relaunch)
    return back


# ------------------------------------------------------------------ projects

def unfinished_projects(statuses):
    """Projects where a version change would make the Methods cite two versions:
    a blind set is built (log.md records the version) and results are not unblinded yet."""
    out = []
    for p in statuses:
        if p.get("coded") is not None and not p.get("unblinded"):
            name = p.get("name") or Path(p["path"]).name
            out.append({"name": name, "path": p["path"], "scoring_started": bool(p.get("scoring_started"))})
    return out


def warning_text(projects, new):
    if not projects:
        return ""
    started = [p["name"] for p in projects if p["scoring_started"]]
    built = [p["name"] for p in projects if not p["scoring_started"]]
    parts = []
    if started:
        parts.append(f"Scoring is in progress in {', '.join(started)}.")
    if built:
        parts.append(f"A blind set is built but not unblinded in {', '.join(built)}; log.md recorded "
                     "the wormscore version when it was built.")
    parts.append(f"Updating to {new} now means your Methods would cite two wormscore versions. "
                 "Update before you build the blind set or after you unblind.")
    return " ".join(parts)
