"""Inline-SVG charts for the homepage, drawn from the derived CSVs in content/data.

No chart library and no client-side JavaScript: each chart is an SVG string the
template drops into the page. Colours, type and rules come from style.css, so the
charts restyle with the rest of the site.

Run prepare_data.py to refresh the underlying numbers.
"""

import csv
import datetime as dt
import json
import math
from html import escape
from pathlib import Path

DATA = Path(__file__).parent / "content" / "data"

S1 = "var(--s1)"      # clay — the primary series in every chart
S2 = "var(--s2)"      # blue — the comparison series
CTX = "var(--s-ctx)"  # grey — context marks that carry no identity

DAY = dt.timedelta(days=1)


# ---------------------------------------------------------------- data loading

_cache = {}


def load():
    """Read the derived data, reloading when prepare_data.py has rewritten it."""
    stamp = max((p.stat().st_mtime for p in DATA.glob("*")), default=0)
    if _cache.get("stamp") != stamp:
        rows = lambda name: list(csv.DictReader((DATA / name).open(encoding="utf-8")))
        _cache.update(
            stamp=stamp,
            stats=json.loads((DATA / "stats.json").read_text()),
            eci=rows("eci.csv"),
            compute=rows("compute_stock.csv"),
            build=rows("build_time.csv"),
        )
    return _cache


# ------------------------------------------------------------------- svg bricks

W, H = 1000, 500          # viewBox; the figure scales to its container
PAD = {"t": 44, "r": 192, "b": 46, "l": 62}
PLOT_W = W - PAD["l"] - PAD["r"]
PLOT_H = H - PAD["t"] - PAD["b"]


def date_of(s):
    return dt.date.fromisoformat(s)


class Scale:
    """Linear (or log10) mapping from data to pixels."""

    def __init__(self, lo, hi, px_lo, px_hi, log=False):
        self.lo, self.hi, self.log = lo, hi, log
        self.px_lo, self.px_hi = px_lo, px_hi

    def __call__(self, v):
        lo, hi, v = (self.lo, self.hi, v)
        if self.log:
            lo, hi, v = math.log10(lo), math.log10(hi), math.log10(max(v, 1e-9))
        t = (v - lo) / (hi - lo)
        return self.px_lo + t * (self.px_hi - self.px_lo)


def days(d, origin):
    return (d - origin).days


def text(x, y, s, cls="c-tick", anchor="start", dy=0):
    return (f'<text class="{cls}" x="{x:.1f}" y="{y + dy:.1f}" text-anchor="{anchor}">'
            f'{escape(str(s))}</text>')


def dot(x, y, r, fill, title=None, ring=True):
    """A marker with the 2px surface ring that keeps overlapping points legible."""
    t = f"<title>{escape(title)}</title>" if title else ""
    ring_attr = ' stroke="var(--bg)" stroke-width="2"' if ring else ""
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}"{ring_attr}>{t}</circle>'


def path(points, stroke, width=2, dash=None):
    if not points:
        return ""
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round"{dash_attr}/>')


def grid_line(x1, y1, x2, y2):
    return f'<line class="c-grid" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'


def legend(entries, x, y):
    """entries: (colour, label, style) where style is 'line', 'dot' or 'hollow'."""
    out = []
    for i, (colour, label, style) in enumerate(entries):
        yy = y + i * 22
        if style == "line":
            out.append(f'<line x1="{x}" y1="{yy}" x2="{x + 18}" y2="{yy}" stroke="{colour}" '
                       f'stroke-width="2" stroke-linecap="round"/>')
            out.append(dot(x + 9, yy, 4, colour, ring=False))
        elif style == "hollow":
            out.append(f'<circle cx="{x + 9}" cy="{yy}" r="4" fill="var(--bg)" stroke="{colour}" stroke-width="2"/>')
        else:
            out.append(dot(x + 9, yy, 4.5, colour, ring=False))
        out.append(text(x + 26, yy + 4, label, "c-legend"))
    return "".join(out)


def frame(body, label):
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}" '
            f'xmlns="http://www.w3.org/2000/svg" class="chart-svg">{body}</svg>')


def fmt_compact(v):
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= cut:
            s = f"{v / cut:.1f}".rstrip("0").rstrip(".")
            return f"{s}{suffix}"
    return f"{v:,.0f}"


# ------------------------------------------------------- 1. capability frontier

def chart_capability():
    d = load()
    stats = d["stats"]
    rows = d["eci"]

    x_lo, x_hi = dt.date(2023, 1, 1), dt.date(2027, 1, 1)
    y_lo, y_hi = 100, 180
    sx = Scale(0, days(x_hi, x_lo), PAD["l"], PAD["l"] + PLOT_W)
    sy = Scale(y_lo, y_hi, PAD["t"] + PLOT_H, PAD["t"])
    px = lambda date: sx(days(date, x_lo))

    out = []
    for v in range(y_lo, y_hi + 1, 20):
        out.append(grid_line(PAD["l"], sy(v), PAD["l"] + PLOT_W, sy(v)))
        out.append(text(PAD["l"] - 12, sy(v) + 4, v, "c-tick", "end"))
    for year in range(2023, 2028):
        xx = px(dt.date(year, 1, 1))
        out.append(text(xx, PAD["t"] + PLOT_H + 24, year, "c-tick", "middle"))

    # Context: every tracked release.
    for r in rows:
        if not r["frontier"]:
            out.append(dot(px(date_of(r["date"])), sy(float(r["eci"])), 3.5, CTX,
                           title=f'{r["name"]} · {r["org"]} · {r["date"]} · ECI {r["eci"]}', ring=False))

    series = []
    for group, colour, name in (("closed", S1, "Closed weights"), ("open", S2, "Open weights")):
        pts = [r for r in rows if r["frontier"] == group]
        xy = [(px(date_of(r["date"])), sy(float(r["eci"]))) for r in pts]

        # 90% interval of the frontier models themselves, as a wash.
        ribbon = ([(px(date_of(r["date"])), sy(float(r["ci_high"]))) for r in pts] +
                  [(px(date_of(r["date"])), sy(float(r["ci_low"]))) for r in reversed(pts)])
        out.append(f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in ribbon)}" '
                   f'fill="{colour}" opacity="0.10"/>')
        out.append(path(xy, colour))
        for r, (x, y) in zip(pts, xy):
            out.append(dot(x, y, 4.5, colour,
                           title=f'{r["name"]} · {r["org"]} · {r["date"]} · ECI {r["eci"]}'))
        series.append((group, colour, name, pts, xy))

    # The gap: how long the closed frontier held today's best open score.
    latest_open = [r for r in rows if r["frontier"] == "open"][-1]
    level = float(latest_open["eci"])
    reached = [r for r in rows if r["frontier"] == "closed" and float(r["eci"]) >= level]
    if reached and stats.get("open_lag_months"):
        x1, x2 = px(date_of(reached[0]["date"])), px(date_of(latest_open["date"]))
        y = sy(level)
        out.append(f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2:.1f}" y2="{y:.1f}" class="c-annot-rule"/>')
        for xx in (x1, x2):
            out.append(f'<line x1="{xx:.1f}" y1="{y - 6:.1f}" x2="{xx:.1f}" y2="{y + 6:.1f}" class="c-annot-rule"/>')
        # Right-aligned just inside the span so it clears the end-of-line labels.
        out.append(text(x2 - 8, y - 13, f'[{stats["open_lag_months"]} months behind]', "c-annot", "end"))

    # Direct labels at the line ends, in ink, with the colour carried by a dot.
    for group, colour, name, pts, xy in series:
        x, y = xy[-1]
        out.append(dot(x + 16, y - 10, 4.5, colour, ring=False))
        out.append(text(x + 26, y - 6, name, "c-label"))
        out.append(text(x + 26, y + 11, f'+{stats[f"eci_{group}"]["slope"]} ECI/yr', "c-sub"))

    out.append(text(PAD["l"], PAD["t"] - 14, "Epoch Capabilities Index", "c-axis-title"))
    out.append(text(PAD["l"] + PLOT_W, PAD["t"] - 14,
                    f'{stats["eci_models"]} tracked releases', "c-foot", "end"))

    body = "".join(out) + legend([(S1, "Closed-weights frontier", "line"),
                                  (S2, "Open-weights frontier", "line"),
                                  (CTX, "Other releases", "dot")], W - PAD["r"] + 14, PAD["t"] + 170)
    return frame(body, "Epoch Capabilities Index of frontier AI models, 2023 to today")


# ------------------------------------------------------------- 2. compute stock

def chart_compute():
    d = load()
    stats = d["stats"]["compute"]
    rows = d["compute"]
    today = dt.date.fromisoformat(d["stats"]["as_of"])

    x_lo, x_hi = dt.date(2023, 1, 1), dt.date(2029, 1, 1)
    y_lo, y_hi = 1e4, 1e8
    sx = Scale(0, days(x_hi, x_lo), PAD["l"], PAD["l"] + PLOT_W)
    sy = Scale(y_lo, y_hi, PAD["t"] + PLOT_H, PAD["t"], log=True)
    px = lambda date: sx(days(date, x_lo))

    out = []
    for p in range(4, 9):
        v = 10 ** p
        out.append(grid_line(PAD["l"], sy(v), PAD["l"] + PLOT_W, sy(v)))
        out.append(text(PAD["l"] - 12, sy(v) + 4, fmt_compact(v), "c-tick", "end"))
    for year in range(2023, 2030):
        out.append(text(px(dt.date(year, 1, 1)), PAD["t"] + PLOT_H + 24, year, "c-tick", "middle"))

    # "Today" divider: everything to its right is Epoch's estimate of announced builds.
    xt = px(today)
    out.append(f'<line x1="{xt:.1f}" y1="{PAD["t"]}" x2="{xt:.1f}" y2="{PAD["t"] + PLOT_H}" class="c-rule"/>')
    out.append(text(xt + 8, PAD["t"] + 14, "[today]", "c-annot"))
    out.append(text(xt + 8, PAD["t"] + 30, "announced builds →", "c-foot"))

    for field, colour, name in (("total_h100e", S1, "All tracked sites"),
                                ("largest_site_h100e", S2, "Largest single site")):
        pts = [(px(date_of(r["date"])), sy(float(r[field])), r) for r in rows if float(r[field]) > 0]
        solid = [(x, y) for x, y, r in pts if r["observed"] == "1"]
        dashed = [(x, y) for x, y, r in pts if r["observed"] != "1"]
        out.append(path(solid, colour))
        out.append(path(solid[-1:] + dashed, colour, dash="2 7"))
        x, y, r = pts[-1]
        xl, yl, rl = [p for p in pts if p[2]["observed"] == "1"][-1]
        out.append(dot(xl, yl, 5, colour, title=f'{name}: {fmt_compact(float(rl[field]))} H100e on {rl["date"]}'))
        out.append(dot(x + 16, y - 10, 4.5, colour, ring=False))
        out.append(text(x + 26, y - 6, name, "c-label"))
        out.append(text(x + 26, y + 11, f'{fmt_compact(float(rl[field]))} today', "c-sub"))

    out.append(text(PAD["l"], PAD["t"] - 14, "H100-equivalents in tracked AI data centres (log scale)", "c-axis-title"))
    out.append(text(PAD["l"] + 12, sy(3e7), f'[×{stats["multiple_per_year"]} per year]', "c-annot"))
    out.append(text(PAD["l"] + 12, sy(3e7) + 17,
                    f'doubling every {stats["doubling_months"]} months · R² {stats["r2"]}', "c-foot"))
    out.append(text(PAD["l"] + PLOT_W, PAD["t"] - 14,
                    f'{stats["centers"]} data centres tracked', "c-foot", "end"))

    body = "".join(out) + legend([(S1, "All tracked sites", "line"),
                                  (S2, "Largest single site", "line")], W - PAD["r"] + 14, PAD["t"] + 200)
    return frame(body, "Compute in tracked AI data centres, H100-equivalents, log scale")


# ---------------------------------------------------------------- 3. build time

def chart_build():
    d = load()
    stats = d["stats"]["build"]
    rows = d["build"]

    x_lo, x_hi = dt.date(2021, 1, 1), dt.date(2029, 7, 1)
    y_lo, y_hi = 0, 70
    sx = Scale(0, days(x_hi, x_lo), PAD["l"], PAD["l"] + PLOT_W)
    sy = Scale(y_lo, y_hi, PAD["t"] + PLOT_H, PAD["t"])
    px = lambda date: sx(days(date, x_lo))

    out = []
    for v in range(0, y_hi + 1, 12):
        out.append(grid_line(PAD["l"], sy(v), PAD["l"] + PLOT_W, sy(v)))
        out.append(text(PAD["l"] - 12, sy(v) + 4, v, "c-tick", "end"))
    for year in range(2021, 2030, 1):
        out.append(text(px(dt.date(year, 1, 1)), PAD["t"] + PLOT_H + 24, year, "c-tick", "middle"))

    median = stats["median_months"]
    out.append(f'<line x1="{PAD["l"]:.1f}" y1="{sy(median):.1f}" x2="{PAD["l"] + PLOT_W:.1f}" '
               f'y2="{sy(median):.1f}" class="c-annot-rule"/>')
    out.append(text(PAD["l"] + 12, sy(median) - 10, f"[median {median} months]", "c-annot"))

    for r in rows:
        x, y = px(date_of(r["operational_date"])), sy(min(float(r["months"]), y_hi))
        done = r["observed"] == "1"
        title = (f'{r["name"]} · {r["months"]} months · first building live {r["operational_date"]}'
                 f'{"" if done else " (projected)"}')
        if done:
            out.append(dot(x, y, 5, S1, title=title))
        else:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="var(--bg)" stroke="{S2}" '
                       f'stroke-width="2"><title>{escape(title)}</title></circle>')

    out.append(text(PAD["l"], PAD["t"] - 14, "Months from ground-breaking to first building live", "c-axis-title"))
    out.append(text(PAD["l"] + PLOT_W, PAD["t"] - 14,
                    f'{stats["n_complete"]} built · {stats["n_planned"]} under way', "c-foot", "end"))

    body = "".join(out) + legend([(S1, "Already operating", "dot"),
                                  (S2, "Projected", "hollow")], W - PAD["r"] + 14, PAD["t"] + 210)
    return frame(body, "Construction time for large AI data centres by date first operational")


# ------------------------------------------------------------------ table views

def tables():
    """Plain-text fallback for every chart: the numbers, readable without colour."""
    d = load()
    eci_rows = [r for r in d["eci"] if r["frontier"]][-10:]
    # Roughly every six months (the series steps in 30-day points), plus the last point.
    compute_rows = d["compute"][::6] + [d["compute"][-1]]
    build_rows = sorted((r for r in d["build"] if r["observed"] == "1"),
                        key=lambda r: r["operational_date"], reverse=True)[:10]
    return {
        "capability": (["Model", "Released", "ECI", "Weights"],
                       [[r["name"], r["date"], r["eci"], r["group"]] for r in eci_rows]),
        "compute": (["Date", "All tracked sites", "Largest single site"],
                    [[r["date"], f'{float(r["total_h100e"]):,.0f}', f'{float(r["largest_site_h100e"]):,.0f}']
                     for r in compute_rows]),
        "build": (["Data centre", "First building live", "Months to build"],
                  [[r["name"], r["operational_date"], r["months"]] for r in build_rows]),
    }


def homepage_charts():
    d = load()
    return {"capability": chart_capability(), "compute": chart_compute(), "build": chart_build(),
            "stats": d["stats"], "tables": tables()}
