"""
Lugar Bipartisan Index scraper.

Downloads the Bipartisan Index ranking tables for Congresses 113-118 from
thelugarcenter.org and writes one CSV per Congress-chamber to lugar_data/.

Usage:
    python scrape_lugar.py                # scrape all available Congresses
    python scrape_lugar.py --congress 117 # scrape a single Congress

Output:
    lugar_data/lugar_{congress}_{chamber}.csv
    with columns: rank, first_name, last_name, state, party, lugar_score

Notes:
    - Lugar covers 113-118; 118 is first-year-only (2023).
    - The HTML contains one wide table with columns split into two halves:
      left half is ranked by score, right half is alphabetical. We parse the
      ranked side (cols 0-5) because it's the canonical ordering.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

# ourwork-XX.html page IDs, keyed by (congress, chamber)
# Chamber-level (two-year aggregate) pages
LUGAR_PAGES: dict[tuple[int, str], str] = {
    (114, "Senate"): "ourwork-54.html",
    (114, "House"):  "ourwork-53.html",
    (115, "Senate"): "ourwork-69.html",
    (115, "House"):  "ourwork-68.html",
    (116, "Senate"): "ourwork-80.html",
    (116, "House"):  "ourwork-79.html",
    (117, "Senate"): "ourwork-84.html",
    (117, "House"):  "ourwork-83.html",
    # 118th Congress: only first-year (2023) scores published so far.
    (118, "Senate"): "ourwork-85.html",
    (118, "House"):  "ourwork-86.html",
}

BASE_URL = "https://www.thelugarcenter.org/"
OUTDIR = Path("lugar_data")


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (research/academic)"})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


# Row pattern: a <tr> with exactly 13 <td> cells (ranked | alphabetical).
# We parse <tr>...</tr> blocks, strip <td> contents, and keep rows whose
# first cell is an integer rank.
TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def clean_cell(html: str) -> str:
    text = TAG_RE.sub("", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return text.strip()


def parse_lugar_page(html: str) -> list[dict]:
    """Extract ranked rows from a Lugar scores page.

    Each data row has 13 cells. The left half holds the ranked view:
      [0]=rank, [1]=first_name, [2]=last_name, [3]=state, [4]=party, [5]=score
    """
    rows = []
    for tr_match in TR_RE.finditer(html):
        cells = [clean_cell(c) for c in TD_RE.findall(tr_match.group(1))]
        if len(cells) != 13:
            continue
        rank_str = cells[0]
        if not rank_str.isdigit():
            continue
        try:
            score = float(cells[5])
        except ValueError:
            continue
        rows.append({
            "rank": int(rank_str),
            "first_name": cells[1],
            "last_name": cells[2],
            "state": cells[3],
            "party": cells[4],
            "lugar_score": score,
        })
    return rows


def scrape_one(congress: int, chamber: str, outdir: Path) -> int:
    page = LUGAR_PAGES.get((congress, chamber))
    if page is None:
        print(f"  No page registered for {congress}/{chamber}, skipping.")
        return 0

    url = BASE_URL + page
    print(f"  Fetching {url}")
    html = fetch_html(url)
    rows = parse_lugar_page(html)

    if not rows:
        print(f"  WARNING: parsed 0 rows from {url}")
        return 0

    outpath = outdir / f"lugar_{congress}_{chamber.lower()}.csv"
    with open(outpath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "first_name", "last_name", "state", "party", "lugar_score",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Wrote {len(rows):>4} rows -> {outpath}")
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--congress", type=int, help="Single Congress (e.g., 117)")
    parser.add_argument("--outdir", type=Path, default=OUTDIR)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    if args.congress:
        targets = [(c, ch) for (c, ch) in LUGAR_PAGES if c == args.congress]
        if not targets:
            print(f"Congress {args.congress} not available. Options: "
                  f"{sorted(set(c for c, _ in LUGAR_PAGES))}")
            sys.exit(1)
    else:
        targets = sorted(LUGAR_PAGES.keys())

    total = 0
    for congress, chamber in targets:
        print(f"{congress}th Congress / {chamber}")
        total += scrape_one(congress, chamber, args.outdir)
        time.sleep(0.5)  # be polite

    print(f"\nDone. {total} total rows across {len(targets)} files.")


if __name__ == "__main__":
    main()
