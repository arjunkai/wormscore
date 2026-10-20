# PyInstaller build for the Windows app (one folder, no console window).
# Run from the repository root: packaging\build_windows.bat
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
static = root / "src" / "wormscore" / "static"

a = Analysis(
    [str(root / "packaging" / "wormscore_app.py")],
    pathex=[str(root / "src")],
    datas=[(str(static), "wormscore/static")],
    hiddenimports=collect_submodules("wormscore") + collect_submodules("imagecodecs"),
    excludes=["matplotlib", "pandas", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="wormscore", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="wormscore")
