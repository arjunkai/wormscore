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
