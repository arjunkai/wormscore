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
import errno
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
    # Two settings that only a test sets; the app never sets them and no screen offers them.
    # WORMSCORE_UPDATE_FILE (1.26.9): a file on this computer that holds what GitHub's "latest release"
    # answer would be, with the zip's address as a file:// address. The checks on GitHub's Windows and Mac
    # machines use it to install a newer build over an older one through the real updater, then go back,
    # without a published release. Everything else is the same code as a real update: the zip must match
    # the SHA-256 the file lists, is unpacked, and must start and report the version expected.
    # WORMSCORE_UPDATE_URL: another address, for the tests' stand-in for GitHub.
    local = os.environ.get("WORMSCORE_UPDATE_FILE")
    if local:
        return Path(local).resolve().as_uri()
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


def same_volume(a, b):
    """Whether two paths are on one volume, so a folder can go from one to the other in one rename.
    A path that does not exist yet is judged by the nearest folder above it that does."""
    def device(p):
        p = Path(p)
        while not p.exists() and p != p.parent:
            p = p.parent
        return os.stat(p).st_dev
    try:
        return device(a) == device(b)
    except OSError:
        return True         # cannot tell: as before


NEAR_PREVIOUS, NEAR_STAGE = ".wormscore-previous", ".wormscore-update"     # hidden folders next to the Mac app


def app_paths(kind=None):
    """Where the app is, where the previous version is kept and where a new one is unpacked.
    Windows keeps them next to the app folder, so every move is a rename on one drive.

    macOS keeps them in the app's data folder (~/Library/Application Support/wormscore), which is on
    the app's volume on most Macs. Where it is not (the app on an external disk, or /Applications on
    another volume than the home folder), a move between the two is a copy: it can stop halfway and
    leave a part of an app in the app's place, and the kept copy as a part too. There the previous
    version and the new copy are kept in two hidden folders next to the app instead, so every move of
    the exchange is one rename on the app's volume, which happens whole or not at all."""
    kind = kind or install_kind()
    if kind == "windows":
        cur = Path(sys.executable).resolve().parent
        return {"current": cur, "previous": cur.with_name(cur.name + "-previous"),
                "stage": cur.with_name(cur.name + "-update"), "app": "wormscore.exe", "check": "wormscore.exe"}
    if kind == "mac":
        cur = Path(sys.executable).resolve().parents[2]            # wormscore.app/Contents/MacOS/wormscore
        d = data_dir()
        if same_volume(cur.parent, d):
            return {"current": cur, "previous": d / "previous" / cur.name, "stage": d / "update-stage",
                    "app": "wormscore.app", "check": "Contents/MacOS/wormscore"}
        return {"current": cur, "previous": cur.parent / NEAR_PREVIOUS / cur.name, "stage": cur.parent / NEAR_STAGE,
                "app": "wormscore.app", "check": "Contents/MacOS/wormscore",
                # where an earlier version kept the previous copy on such a Mac: Go back still finds it
                "previous_before": d / "previous" / cur.name}
    return {}


def kept_copy(paths):
    """The folder that holds the previous version, or None."""
    for key in ("previous", "previous_before"):
        if paths.get(key) and paths[key].exists():
            return paths[key]
    return None


def copy_whole(src, dst):
    """A copy of an app folder with its links and permissions (ditto on macOS, as unpack uses)."""
    if sys.platform == "darwin":
        r = subprocess.run(["ditto", str(src), str(dst)], capture_output=True, text=True)
        if r.returncode:
            raise OSError(errno.EIO, r.stderr.strip() or "ditto failed", str(src))
    else:
        shutil.copytree(src, dst, symlinks=True)


def move_whole(src, dst):
    """Move a folder so that dst is either not there or whole. On one volume this is one rename.
    Across volumes a rename is refused (EXDEV), and a plain move would copy file by file straight into
    dst: here the copy is made next to dst under another name and renamed into place when it is
    complete; only then is src removed. If the copy fails, the part of it is removed and src is whole."""
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    part = dst.with_name(dst.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    try:
        copy_whole(src, part)
        os.rename(part, dst)
    except BaseException:
        shutil.rmtree(part, ignore_errors=True)
        raise
    shutil.rmtree(src, ignore_errors=True)


def previous_version():
    """Version of the copy kept for rollback, or None."""
    p = app_paths()
    try:
        info = json.loads((data_dir() / "previous.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if p and kept_copy(p) and parse_version(info.get("version", "")):
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
    ok = bool(lines) and lines[0].strip() == "ok"
    if ok:
        # a kept copy an earlier version left in the data folder of a Mac whose app is on another
        # volume: the exchange that just worked keeps its copy next to the app, so that one is spare
        p = app_paths()
        if p.get("previous_before") and p["previous"].exists():
            shutil.rmtree(p["previous_before"], ignore_errors=True)
    return {"ok": ok, "message": " ".join(lines[1:]).strip()}


# ------------------------------------------------------------------ check

def https_context():
    """Certificates for HTTPS in the packaged Mac app (1.26.5), or None (Python's own, everywhere else).

    Python on macOS does not use the system's certificates. The app's OpenSSL looks for a certificate
    file at the path of the Python it was built with, which exists on the build machine and not on a
    user's Mac, so the update check and the real-worm demo failed there with CERTIFICATE_VERIFY_FAILED.
    The Mac app therefore carries certifi's certificate list and uses it."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    try:
        import ssl
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:       # no certifi in this build: as before
        return None


def https_kwargs():
    """Extra arguments for urlopen: the Mac app's certificates, or nothing (the call is then as before)."""
    ctx = https_context()
    return {"context": ctx} if ctx is not None else {}


def check(timeout=5):
    """The latest release: {version, notes, page, asset}, asset being this platform's zip
    (None for pip installs or when the release has no zip for this platform)."""
    req = urllib.request.Request(latest_url(), headers={"User-Agent": f"wormscore/{__version__}",
                                                        "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, **https_kwargs()) as r:
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
        with urllib.request.urlopen(req, timeout=60, **https_kwargs()) as r, open(part, "wb") as fh:
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


# 1.26.9: every move in the helpers is one rename of a whole folder, which either happens or does not.
# The Windows helper used Move-Item, which, when a file in the app folder is still open in another
# program (a virus scanner, a second copy of the app), moves the files it can one by one and then
# stops: the app folder was left with only the open file, without wormscore.exe, and "Go back" then
# had nothing whole to put back. [System.IO.Directory]::Move fails as a whole instead, so a failed
# swap leaves every copy as it was. The helpers also:
# - write previous.json (which version the kept copy is) only after a swap that worked, so after a
#   failed one "Go back" still names and restores the copy that is really kept;
# - on a failed "Go back", put the kept copy back where it was (-Restore), so it can be tried again;
# - check that the app folder holds the app before reporting "ok".
WINDOWS_HELPER = r"""param([int]$WaitPid, [string]$Current, [string]$New, [string]$Previous, [string]$Result, [string]$Launch,
      [string]$PrevJson, [string]$PrevVersion, [string]$Restore, [string]$Check)
# Written by wormscore: swaps in a new app folder after wormscore has closed.
$ErrorActionPreference = 'Stop'
function Move-Whole($from, $to) {
  # one rename: it fails as a whole if a file inside is open, and never moves part of a folder
  if (Test-Path -LiteralPath $to) { throw "$to is in the way." }
  try { [System.IO.Directory]::Move($from, $to) }
  catch { throw "Could not move $from. A file in it may be open in another program." }
}
function Move-Retry($from, $to) {
  for ($i = 0; ; $i++) {
    try { Move-Whole $from $to; return }
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
  catch { if (Test-Path -LiteralPath $old) { Move-Retry $old $Previous }; throw }
  try { Move-Retry $New $Current }
  catch {
    Move-Retry $Previous $Current
    if (Test-Path -LiteralPath $old) { Move-Retry $old $Previous }
    throw
  }
  if ($Check -and -not (Test-Path -LiteralPath (Join-Path $Current $Check))) {
    # the copy moved in is not a whole app: put the one that worked back
    Move-Retry $Current $New
    Move-Retry $Previous $Current
    if (Test-Path -LiteralPath $old) { Move-Retry $old $Previous }
    throw "The new copy has no $Check."
  }
  # the swap is done: a label that cannot be written must not report it as failed (1.26.10)
  if ($PrevJson) { try { [System.IO.File]::WriteAllText($PrevJson, "{`"version`": `"$PrevVersion`"}") } catch {} }
  if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }
  $stage = Split-Path -Parent $New
  if (-not (Get-ChildItem -LiteralPath $stage -Force)) { Remove-Item -LiteralPath $stage -ErrorAction SilentlyContinue }
} catch {
  $status = @('failed', "$($_.Exception.Message) The previous version was kept.")
  try {
    if ($Restore -and (Test-Path -LiteralPath $New) -and -not (Test-Path -LiteralPath $Restore)) { Move-Retry $New $Restore }
  } catch {}
}
Set-Content -LiteralPath $Result -Value $status -Encoding UTF8
if ($Launch) { Start-Process -FilePath (Join-Path $Current $Launch) -WorkingDirectory $HOME }
"""

POSIX_HELPER = r"""#!/bin/sh
# Written by wormscore: swaps in a new app after wormscore has closed.
# Arguments: wait_pid current new previous result launch(1 or empty) prev_json prev_version restore check stage
pid="$1"; current="$2"; new="$3"; previous="$4"; result="$5"; launch="$6"
prev_json="$7"; prev_version="$8"; restore="$9"; shift 9; check="$1"; stage="$2"
i=0
while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 240 ]; do sleep 0.5; i=$((i+1)); done
# never onto something that is already there: mv would put the folder inside it
retry() { n=0; until [ ! -e "$2" ] && mv "$1" "$2" 2>/dev/null; do n=$((n+1)); [ "$n" -ge 60 ] && return 1; sleep 1; done; }
# Every move below must be one rename. Between two volumes mv copies file by file instead, and a copy
# that stops halfway leaves a part of an app in the app's place. wormscore keeps the copies on the
# app's volume (update.app_paths); if they are not, nothing is moved.
dev() { stat -c %d "$1" 2>/dev/null || stat -f %d "$1" 2>/dev/null; }
one_volume() { [ "$(dev "$1")" = "$(dev "$2")" ]; }
old="$previous.old"
status="ok"; msg=""
rm -rf "$old"
mkdir -p "$(dirname "$previous")"
if [ -e "$current" ] && [ -e "$new" ] && { ! one_volume "$current" "$new" || ! one_volume "$current" "$(dirname "$previous")"; }; then
  status="failed"; msg="The app and the copy to put in its place are on different disks, so nothing was moved."
elif [ -e "$previous" ] && ! retry "$previous" "$old"; then
  status="failed"; msg="Could not move the previous copy aside."
elif ! retry "$current" "$previous"; then
  [ -e "$old" ] && retry "$old" "$previous"
  status="failed"; msg="Could not move the current copy aside."
elif ! retry "$new" "$current"; then
  retry "$previous" "$current"; [ -e "$old" ] && retry "$old" "$previous"
  status="failed"; msg="Could not move the new copy into place."
elif [ -n "$check" ] && [ ! -e "$current/$check" ]; then
  # the copy moved in is not a whole app: put the one that worked back
  retry "$current" "$new"; retry "$previous" "$current"; [ -e "$old" ] && retry "$old" "$previous"
  status="failed"; msg="The new copy is not a whole app."
else
  [ -n "$prev_json" ] && printf '{"version": "%s"}' "$prev_version" > "$prev_json"
  rm -rf "$old"
  rmdir "$(dirname "$new")" 2>/dev/null
  # the rest of the unpacked download (licence, README): only a folder wormscore made for it
  case "$stage" in */update-stage|*/.wormscore-update) rm -rf "$stage";; esac
fi
if [ "$status" = "failed" ]; then
  msg="$msg The previous version was kept."
  if [ -n "$restore" ] && [ -e "$new" ] && [ ! -e "$restore" ]; then retry "$new" "$restore"; fi
fi
printf '%s\n%s\n' "$status" "$msg" > "$result"
[ -n "$launch" ] && open "$current"
exit 0
"""


def start_helper(paths, new, pid=None, relaunch=True, prev_version=None, restore=None):
    """Start the helper that swaps `new` into place once process `pid` (this app) has exited.
    prev_version: what the copy moved aside is, written to previous.json once the swap has worked.
    restore: where `new` goes back to if the swap fails (Go back: the kept previous copy's place)."""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    result = d / "update-result.txt"
    result.unlink(missing_ok=True)
    pid = os.getpid() if pid is None else pid
    prev_json = str(d / "previous.json") if prev_version else ""
    if sys.platform == "win32":
        script = d / "update-helper.ps1"
        script.write_text(WINDOWS_HELPER, encoding="utf-8")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
               "-WaitPid", str(pid), "-Current", str(paths["current"]), "-New", str(new),
               "-Previous", str(paths["previous"]), "-Result", str(result),
               "-Launch", paths["app"] if relaunch else ""]
        # after -Launch, as before; an empty value is left out
        for name, value in (("-PrevJson", prev_json), ("-PrevVersion", prev_version), ("-Restore", restore),
                            ("-Check", paths.get("check"))):
            if value:
                cmd[-2:-2] = [name, str(value)]
        flags = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:   # leave any job object this app runs in, so closing the app does not stop the helper
            return subprocess.Popen(cmd, cwd=str(Path.home()), creationflags=flags | 0x01000000, close_fds=True)
        except OSError:
            return subprocess.Popen(cmd, cwd=str(Path.home()), creationflags=flags, close_fds=True)
    script = d / "update-helper.sh"
    script.write_text(POSIX_HELPER, encoding="utf-8")
    return subprocess.Popen(["/bin/sh", str(script), str(pid), str(paths["current"]), str(new),
                             str(paths["previous"]), str(result), "1" if relaunch else "",
                             prev_json, prev_version or "", str(restore or ""), paths.get("check") or "",
                             str(paths.get("stage") or "")],
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
    start_helper(paths, new, relaunch=relaunch, prev_version=__version__)


def rollback(relaunch=True):
    """Swap the kept previous version back in (this version becomes the previous one)."""
    kind = install_kind()
    back = previous_version()
    if kind not in ASSETS or not back:
        raise UpdateError("There is no previous version to go back to.")
    paths = app_paths(kind)
    # 1.26.10: the kept copy is checked before anything is touched, and moved with one rename or not at
    # all. A copy-and-delete (the fallback until 1.26.9) could stop halfway when a file of the kept copy
    # was open in another program; the next Go back then put a folder with one file in the app's place.
    kept = kept_copy(paths)
    if paths.get("check") and not (kept / paths["check"]).exists():
        raise UpdateError("The kept copy of the previous version is not complete, so nothing was changed. "
                          "To use another version, download it from the Releases page.")
    if paths["stage"].exists():
        shutil.rmtree(paths["stage"])
    paths["stage"].mkdir(parents=True)
    new = paths["stage"] / paths["previous"].name
    try:
        # one rename; from another volume (a copy an earlier version kept there on a Mac) a whole copy
        # is made beside the target first, so the stage never holds a part of an app
        move_whole(kept, new)
    except OSError:
        raise UpdateError("The kept copy of the previous version could not be moved: a file in it may be open in "
                          "another program, or the disk is full. Nothing was changed. Close other programs and "
                          "try again.") from None
    # if the swap fails, the helper puts the kept copy back where it was, so Go back can be tried again
    start_helper(paths, new, relaunch=relaunch, prev_version=__version__, restore=paths["previous"])
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
