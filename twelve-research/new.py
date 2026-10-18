#!/usr/bin/env python3
"""Start a new report.

    python3 new.py "Why The Market Is Wrong About XYZ"
    python3 new.py "Quick Note On ABC" --free --tickers ABC

Creates content/articles/<slug>.md with the front matter filled in and opens it
in your editor. Then write the report, refresh the site, and publish.
"""

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ARTICLES = Path(__file__).parent / "content" / "articles"

TEMPLATE = """---
title: {title}
date: {date}
summary: {summary}
tickers: {tickers}
access: {access}
seeking_alpha_url:
substack_url:
draft: true
---

Opening paragraphs go here. This part is the free preview that everyone sees.

<!-- paywall -->

## The thesis

Everything below the marker is only sent to paying members.
"""


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


def main():
    parser = argparse.ArgumentParser(description="Start a new Twelve Research report.")
    parser.add_argument("title")
    parser.add_argument("--free", action="store_true", help="no paywall; open to everyone")
    parser.add_argument("--tickers", default="", help='e.g. "NVDA, TSM"')
    parser.add_argument("--summary", default="", help="one-sentence dek shown in lists")
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    path = ARTICLES / f"{slugify(args.title)}.md"
    if path.exists():
        sys.exit(f"{path} already exists — edit that file instead.")

    path.write_text(TEMPLATE.format(
        title=args.title, date=args.date, summary=args.summary,
        tickers=args.tickers, access="free" if args.free else "members"))

    print(f"Created {path}")
    print(f"Preview at http://localhost:8000/research/{path.stem} once you set draft: false")
    subprocess.run(["open", str(path)], check=False)


if __name__ == "__main__":
    main()
