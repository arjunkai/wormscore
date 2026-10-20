# PyInstaller build for the Windows app (one folder, no console window).
# Run from the repository root: packaging\build_windows.bat
import importlib.metadata as md
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
static = root / "src" / "wormscore" / "static"


def third_party_licenses(toc, out):
    """Write the license text of every bundled package (and of Python itself) to one file,
    as their licenses require for binary copies."""
    tops = {entry[0].replace("\\", "/").split("/")[0].split(".")[0] for entry in toc}
    owners = md.packages_distributions()
    dists = sorted({d for t in tops for d in owners.get(t, [])} - {"wormscore"})
    base = Path(sys.base_prefix)
    parts = [("Python", base / "LICENSE.txt")] + [(p.parent.name, p) for p in sorted((base / "tcl").glob("*/license.terms"))]
    for name in dists:
        d = md.distribution(name)
        for f in d.files or []:
            if any(w in f.name.upper() for w in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                parts.append((f"{name} {d.version}", Path(d.locate_file(f))))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("wormscore is MIT licensed (see LICENSE). It bundles the software below, "
                 "each under its own license.\n")
        for name, path in parts:
            if path.is_file():
                fh.write(f"\n{'=' * 78}\n{name}: {path.name}\n{'=' * 78}\n\n")
                fh.write(path.read_text(encoding="utf-8", errors="replace"))


a = Analysis(
    [str(root / "packaging" / "wormscore_app.py")],
    pathex=[str(root / "src")],
    datas=[(str(static), "wormscore/static")],
    hiddenimports=collect_submodules("wormscore") + collect_submodules("imagecodecs"),
    excludes=["matplotlib", "pandas", "IPython", "pytest", "setuptools", "pkg_resources"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="wormscore", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="wormscore")

# License notices go next to wormscore.exe, where people look for them.
app = Path(DISTPATH) / "wormscore"
shutil.copy(root / "LICENSE", app / "LICENSE.txt")
third_party_licenses(a.pure + a.binaries + a.datas, app / "THIRD_PARTY_LICENSES.txt")
