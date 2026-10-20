"""Copy a data folder and prove the copy is exact.

Every file (hidden ones included) is copied with its timestamp, then compared with the
original by SHA-256. Copies are set read-only. Nothing in the source is changed.
"""
import shutil
import stat
from pathlib import Path

from .readers import sha256


def copy_verified(src, dst, log=print):
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise SystemExit(f"{src} is not a folder.")
    files = sorted(p for p in src.rglob("*") if p.is_file())
    bad = []
    for n, p in enumerate(files, start=1):
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.copy2(p, target)
        if sha256(p) != sha256(target):
            bad.append(str(p))
        target.chmod(stat.S_IREAD)
        if n % 25 == 0 or n == len(files):
            log(f"copied and checked {n}/{len(files)}")
    if bad:
        raise SystemExit(f"{len(bad)} files did not match after copying: {bad[:5]}")
    log(f"All {len(files)} files copied to {dst} and verified by SHA-256. Copies are read-only.")
    return len(files)
