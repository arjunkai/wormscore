"""Interim report (1.17.0): totals of the answers saved so far, while scoring is still going on.

Blind by construction: the report pools every scored image. It shows nothing by genotype and nothing
about hidden repeats, not even counts, because a scorer could open it after each save and see which
group's count went up, which would reveal that image's genotype (or that it was a repeat). So it holds
only totals of the scorer's own answers, which the scorer already knows:
- progress, unusable images and why, the calls, confidence;
- drift: the share of animals with a defect call in the first and the second half of the images, in the
  order they were scored. Codes are in random order, so both halves have the same mix of genotypes
  on average, and a large difference suggests the criteria are being applied differently over time;
- pace: the median time per image, and the time left at that pace.

Each look is written to log.md as an interim look, and the Methods draft says how many were taken.
"""
import datetime as dt
import json
from collections import Counter
from pathlib import Path

from . import config as C
from .files import append_log, read_csv

LOG_TITLE = "interim look (no results by genotype)"
BREAK_MINUTES = 30      # a longer gap between two saves is a break, not time spent on an image
MIN_HALF = 10           # fewer images in a half: no drift comparison
DRIFT_POINTS = 20       # a difference of at least this many percentage points is pointed out


def _scores(project):
    f = Path(project) / "scoring" / "scores.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def _coded(project):
    key = Path(project) / "key" / "key.csv"
    if key.is_file():
        return len(read_csv(key))
    meta = Path(project) / "blind"
    return sum(1 for d in meta.iterdir() if d.is_dir()) if meta.is_dir() else 0


def _time(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _pct(k, n):
    return round(100 * k / n) if n else None


def report(project):
    """The interim totals: a dict the home page shows and log.md records."""
    project = Path(project)
    cfg = C.load(project)
    sc = cfg["scoring"]
    n_neurons = len(sc["neurons"])
    scores = _scores(project)
    rows = sorted(scores.values(), key=lambda s: s.get("saved", ""))
    usable = [s for s in rows if s.get("usable") == "yes"]
    calls = Counter(s.get(f"n{i}_call", "") for s in usable for i in range(1, n_neurons + 1))
    calls.pop("", None)

    def affected(s):
        got = [s.get(f"n{i}_call", "") for i in range(1, n_neurons + 1)]
        return None if not all(got) else any(c in sc["defect_calls"] for c in got)
    judged = [a for a in (affected(s) for s in usable) if a is not None]
    half = len(usable) // 2
    first = [a for a in (affected(s) for s in usable[:half]) if a is not None]
    second = [a for a in (affected(s) for s in usable[half:]) if a is not None]
    drift = None
    if len(first) >= MIN_HALF and len(second) >= MIN_HALF:
        a, b = _pct(sum(first), len(first)), _pct(sum(second), len(second))
        drift = {"first": a, "second": b, "n_first": len(first), "n_second": len(second),
                 "large": abs(a - b) >= DRIFT_POINTS}
    times = [t for t in (_time(s.get("saved")) for s in rows) if t]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    gaps = sorted(g for g in gaps if 0 < g <= BREAK_MINUTES)
    per_image = gaps[len(gaps) // 2] if gaps else None
    coded = _coded(project)
    left = max(coded - len(scores), 0)
    return {
        "scored": len(scores), "coded": coded, "left": left,
        "usable": len(usable), "unusable": len(rows) - len(usable),
        "unusable_reasons": dict(Counter(s.get("unusable_reason") or "no reason given"
                                         for s in rows if s.get("usable") == "no").most_common()),
        "calls": {c: calls.get(c, 0) for c in sc["calls"]}, "neurons": sum(calls.values()),
        "defect_calls": list(sc["defect_calls"]),
        "affected": sum(judged), "judged": len(judged), "affected_pct": _pct(sum(judged), len(judged)),
        "confidence": {c: sum(1 for s in usable if s.get("confidence") == c) for c in ("sure", "fairly sure", "unsure")},
        "drift": drift,
        "minutes_per_image": round(per_image, 1) if per_image is not None else None,
        "minutes_left": round(per_image * left) if per_image is not None else None,
    }


def lines(rep):
    """The report as log.md lines."""
    out = [f"Scored {rep['scored']} of {rep['coded']} images ({rep['usable']} usable, {rep['unusable']} not usable)."]
    if rep["neurons"]:
        out.append("Calls: " + ", ".join(f"{c} {n}" for c, n in rep["calls"].items()) + f" ({rep['neurons']} neurons).")
    if rep["judged"]:
        out.append(f"Animals with a defect call: {rep['affected']} of {rep['judged']} ({rep['affected_pct']}%), "
                   "all genotypes together.")
    if rep["drift"]:
        d = rep["drift"]
        out.append(f"First half: {d['first']}% with a defect call; second half: {d['second']}%.")
    out.append("No result by genotype and nothing about hidden repeats was shown.")
    return out


def record(project, rep):
    append_log(project, LOG_TITLE, lines(rep))


def looks(project):
    """How many interim looks log.md records."""
    f = Path(project) / "log.md"
    if not f.is_file():
        return 0
    return sum(1 for line in f.read_text(encoding="utf-8").splitlines()
               if line.startswith("## ") and line.endswith(LOG_TITLE))


def methods_sentence(project):
    """For the Methods draft, or "" if no interim look was taken (the Methods then read as before)."""
    n = looks(project)
    if not n:
        return ""
    return (f"During scoring the scorer looked {n} time{'s' if n != 1 else ''} at interim totals of their own "
            "answers, pooled over all images with no result by genotype and nothing about hidden repeats "
            "(recorded in the project log). ")
