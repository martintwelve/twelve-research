#!/usr/bin/env python3
"""Rebuild the homepage chart data from Epoch AI's public datasets.

    python3 prepare_data.py            # re-download and rebuild
    python3 prepare_data.py --offline  # rebuild from the cached raw files

Raw downloads are cached in content/data/raw/. The derived files the site reads
are content/data/*.csv plus stats.json. Epoch AI's data is CC BY 4.0: the charts
credit them, and that credit has to stay on the page.
"""

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "content" / "data"
RAW = DATA / "raw"

SOURCES = {
    "eci_scores.csv": "https://epoch.ai/data/eci_scores.csv",
    "data_center_timelines.csv": "https://epoch.ai/data/data_centers/data_center_timelines.csv",
}

START = dt.date(2023, 1, 1)       # x-axis start for the capability and compute charts
HORIZON = dt.date(2029, 1, 1)     # how far to carry announced build-out


def download(url):
    """requests first (it ships its own CA bundle; the python.org build has none),
    then plain urllib for anyone running this without the dependencies installed."""
    try:
        import requests
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.content
    except ImportError:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()


def fetch(offline=False):
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = RAW / name
        if offline:
            if not path.exists():
                raise SystemExit(f"No cached copy of {name}. Run without --offline once.")
            continue
        print(f"Downloading {url}")
        path.write_bytes(download(url))
    return {name: list(csv.DictReader((RAW / name).open(encoding="utf-8"))) for name in SOURCES}


def parse_date(s):
    return dt.date.fromisoformat(s.strip()[:10])


def fit(xs, ys):
    """Least-squares slope, intercept, R^2."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    total = sum((y - my) ** 2 for y in ys)
    return slope, intercept, (1 - resid / total if total else 0.0)


def years_since(date, origin=START):
    return (date - origin).days / 365.25


# ------------------------------------------------------------------ capability

def build_eci(rows, stats):
    """Every tracked release, with a flag marking the open- and closed-weights frontier."""
    models = []
    for r in rows:
        if not (r.get("date") and r.get("eci")):
            continue
        group = {"Closed weights": "closed", "Open weights": "open"}.get(r["Accessibility group"], "other")
        models.append({
            "date": parse_date(r["date"]), "eci": float(r["eci"]),
            "ci_low": float(r["eci_ci_low"] or r["eci"]), "ci_high": float(r["eci_ci_high"] or r["eci"]),
            "name": r["Display name"] or r["Model"], "org": r["Organization"], "group": group,
        })
    models.sort(key=lambda m: m["date"])

    frontier = {}
    for group in ("closed", "open"):
        best = -math.inf
        frontier[group] = []
        for m in models:
            if m["group"] == group and m["eci"] > best:
                best = m["eci"]
                m["frontier"] = group
                frontier[group].append(m)

    with (DATA / "eci.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "eci", "ci_low", "ci_high", "group", "frontier", "name", "org"])
        for m in models:
            w.writerow([m["date"], round(m["eci"], 2), round(m["ci_low"], 2), round(m["ci_high"], 2),
                        m["group"], m.get("frontier", ""), m["name"], m["org"]])

    for group in ("closed", "open"):
        pts = frontier[group]
        slope, intercept, r2 = fit([years_since(m["date"]) for m in pts], [m["eci"] for m in pts])
        stats[f"eci_{group}"] = {
            "slope": round(slope, 1), "intercept": round(intercept, 2), "r2": round(r2, 2),
            "n": len(pts), "latest": pts[-1]["name"], "latest_eci": round(pts[-1]["eci"], 1),
            "latest_date": pts[-1]["date"].isoformat(),
        }

    # How far behind is the open-weights frontier? For each recent open-weights SOTA,
    # how long before it was the date a closed model first reached that score.
    lags = []
    for m in frontier["open"][-8:]:
        reached = [c["date"] for c in frontier["closed"] if c["eci"] >= m["eci"]]
        if reached:
            lags.append((m["date"] - min(reached)).days)
    stats["open_lag_months"] = round(statistics.median(lags) / 30.44, 1) if lags else None
    stats["eci_models"] = len(models)
    stats["eci_latest_date"] = models[-1]["date"].isoformat()


# --------------------------------------------------------------- compute stock

def center_series(rows, field):
    """Per data center, its (date, value) steps sorted in time."""
    by_center = defaultdict(list)
    for r in rows:
        if r.get(field):
            by_center[r["Data center"]].append((parse_date(r["Date"]), float(r[field] or 0)))
    for v in by_center.values():
        v.sort()
    return by_center


def build_compute(rows, stats, today):
    by_center = center_series(rows, "H100 equivalents")

    def snapshot(when):
        total = largest = 0.0
        for steps in by_center.values():
            current = 0.0
            for date, value in steps:
                if date <= when:
                    current = value
            total += current
            largest = max(largest, current)
        return total, largest

    points, date = [], START
    while date <= HORIZON:
        total, largest = snapshot(date)
        if total > 0:
            points.append((date, total, largest, date <= today))
        date += dt.timedelta(days=30)

    with (DATA / "compute_stock.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "total_h100e", "largest_site_h100e", "observed"])
        for date, total, largest, observed in points:
            w.writerow([date, round(total), round(largest), int(observed)])

    observed = [(d, t) for d, t, _, o in points if o]
    slope, _, r2 = fit([years_since(d) for d, _ in observed], [math.log2(t) for _, t in observed])
    stats["compute"] = {
        "doublings_per_year": round(slope, 2), "doubling_months": round(12 / slope, 1),
        "multiple_per_year": round(2 ** slope, 1), "r2": round(r2, 2),
        "today_total": round(observed[-1][1]), "today_largest": round(max(p[2] for p in points if p[3])),
        "centers": len(by_center),
    }


# ------------------------------------------------------------------ build time

def build_times(rows, stats, today):
    by_center = defaultdict(list)
    for r in rows:
        by_center[r["Data center"]].append(
            (parse_date(r["Date"]), float(r["Buildings operational"] or 0), float(r["Power (MW)"] or 0)))

    records = []
    for name, steps in by_center.items():
        steps.sort()
        start = steps[0][0]
        live = next((d for d, buildings, _ in steps if buildings > 0), None)
        if live and live > start:
            peak_mw = max(mw for _, _, mw in steps)
            records.append((live, (live - start).days / 30.44, name, live <= today, peak_mw))
    records.sort()

    with (DATA / "build_time.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["operational_date", "months", "name", "observed", "peak_mw"])
        for live, months, name, observed, mw in records:
            w.writerow([live, round(months, 1), name, int(observed), round(mw)])

    done = [r for r in records if r[3]]
    slope, _, r2 = fit([years_since(r[0], dt.date(2021, 1, 1)) for r in done], [r[1] for r in done])
    stats["build"] = {
        "median_months": round(statistics.median([r[1] for r in done]), 1),
        "n_complete": len(done), "n_planned": len(records) - len(done),
        "trend_months_per_year": round(slope, 1), "r2": round(r2, 2),
        "fastest": round(min(r[1] for r in done), 1), "slowest": round(max(r[1] for r in done), 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="rebuild from cached downloads")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    raw = fetch(args.offline)
    today = dt.date.today()
    stats = {"as_of": today.isoformat(), "source": "Epoch AI", "license": "CC BY 4.0"}

    build_eci(raw["eci_scores.csv"], stats)
    build_compute(raw["data_center_timelines.csv"], stats, today)
    build_times(raw["data_center_timelines.csv"], stats, today)

    (DATA / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
