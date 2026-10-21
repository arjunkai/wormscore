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
    notes = []
    for name in dists:
        d = md.distribution(name)
        found = False
        for f in d.files or []:
            if any(w in f.name.upper() for w in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                parts.append((f"{name} {d.version}", Path(d.locate_file(f))))
                found = True
        if not found:   # no license file shipped: name the license its metadata gives
            meta = d.metadata
            lic = meta.get("License-Expression") or meta.get("License") or "; ".join(
                c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License"))
            notes.append(f"{name} {d.version}: {lic or 'see the package'} ({meta.get('Home-page') or ''})")
    if any(n.startswith("pywebview") for n in dists):
        notes.append("Microsoft.Web.WebView2.Core.dll, Microsoft.Web.WebView2.WinForms.dll (inside pywebview): "
                     "Copyright (C) Microsoft Corporation, redistributed under the Microsoft WebView2 SDK license, "
                     "https://www.nuget.org/packages/Microsoft.Web.WebView2")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("wormscore is MIT licensed (see LICENSE). It bundles the software below, "
                 "each under its own license.\n")
        if notes:
            fh.write(f"\n{'=' * 78}\nPackages without a license file\n{'=' * 78}\n\n" + "\n".join(notes) + "\n")
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
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="wormscore", console=False,
          icon=str(root / "packaging" / "wormscore.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="wormscore")

# macOS: also wrap the folder as wormscore.app, which is what Mac users double-click.
if sys.platform == "darwin":
    import re
    version = re.search(r'__version__ = "([^"]+)"', (root / "src" / "wormscore" / "__init__.py").read_text()).group(1)
    BUNDLE(coll, name="wormscore.app", icon=str(root / "packaging" / "icon-1024.png"), bundle_identifier="io.github.arjunkai.wormscore",
           info_plist={"CFBundleShortVersionString": version, "CFBundleVersion": version,
                       "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"})

# License notices go next to wormscore.exe, where people look for them.
app = Path(DISTPATH) / "wormscore"
shutil.copy(root / "LICENSE", app / "LICENSE.txt")
third_party_licenses(a.pure + a.binaries + a.datas, app / "THIRD_PARTY_LICENSES.txt")
