"""Small file helpers (CSV tables and log.md) with no numpy, scipy or image readers.

The home screen reads a project's status with these, so the project list appears at once instead
of waiting a few seconds for the image libraries, which load in the background (server.heavy_imports).
pipeline, unblind and the others import them from here.
"""
import csv
import datetime as dt
import json
from pathlib import Path


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


APP_FOLDERS = ("key", "blind", "scoring", "outputs", "practice", "scorers", "config_backups")


def refuse_app_folder(project, destination, what):
    """Stop only if destination is the project folder itself or inside one of the folders wormscore writes
    (a package or practice set there could overwrite or be overwritten). Elsewhere in the project is fine."""
    project, dest = Path(project).resolve(), Path(destination).resolve()
    if dest == project or any(dest == project / f or (project / f) in dest.parents for f in APP_FOLDERS):
        raise SystemExit(f"Choose another folder for the {what}: this one is part of the project's own files "
                         f"({', '.join(APP_FOLDERS[:4])}, ...).")


def plural(n, word, many=None):
    """'1 image', '3 images' (for messages people read; 1.23.1)."""
    return f"{n} {word if n == 1 else many or word + 's'}"


def append_log(project, title, lines, cfg=None):
    with open(Path(project) / "log.md", "a", encoding="utf-8") as fh:
        fh.write(f"\n## {now()} {title}\n")
        for line in lines:
            fh.write(f"- {line}\n")
        if cfg is not None:
            fh.write("- Parameters (merged config.json at the time of this run):\n\n```json\n")
            fh.write(json.dumps(cfg, indent=2))
            fh.write("\n```\n")


def recorded(project, first_score, prefix):
    """The settings last written to log.md under this prefix before the first score was saved
    (any time, if nothing is scored), or None if never recorded."""
    log_path = project / "log.md"
    rec, ts = None, ""
    for line in (log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []):
        if line.startswith("## "):
            ts = line[3:19]
        elif line.startswith("- " + prefix) and (not first_score or ts <= first_score[:16]):
            rec = line[2 + len(prefix):]
    return rec
