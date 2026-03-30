"""
Stage 2: Bipartite Projection
==============================
Reads bill JSON from Stage 1, projects the bipartite (bill-legislator) network
into a one-mode (legislator-legislator) weighted network.

Input:  bills_by_congress/bills_{congress}.json
Output: networks/network_{congress}.json

Usage:
    python build_network.py                        # all 108-119
    python build_network.py --congress 118          # single congress
    python build_network.py --start 110 --end 119   # range
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
BILLS_DIR = SCRIPT_DIR / "bills_by_congress"
OUTPUT_DIR = SCRIPT_DIR / "networks"
DEFAULT_START = 108
DEFAULT_END = 119


def congress_to_years(congress: int) -> str:
    start_year = 1789 + (congress - 1) * 2
    return f"{start_year}-{start_year + 2}"


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def determine_chamber(bill_type: str) -> str:
    """Determine chamber from bill type: hr/hjres -> House, s/sjres -> Senate."""
    bt = bill_type.lower()
    if bt in ("hr", "hjres"):
        return "House"
    elif bt in ("s", "sjres"):
        return "Senate"
    return "Unknown"


def project_congress(congress: int) -> dict | None:
    """Project a single congress's bill data into a legislator-legislator network."""
    filepath = BILLS_DIR / f"bills_{congress}.json"
    if not filepath.exists():
        print(f"  WARNING: {filepath} not found, skipping.")
        return None

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    bills = data["bills"]
    years = congress_to_years(congress)
    print(f"\n  {ordinal(congress)} Congress ({years}): {len(bills)} bills")

    # ── Pass 1: Identify all legislators and compute bipartite degrees ──
    legislators = {}
    bipartite_degrees = defaultdict(int)  # bioguide_id -> bills with cosponsors only
    bill_participation = defaultdict(lambda: {
        "total": 0, "bipartisan": 0, "by_policy": defaultdict(int)
    })
    total_bills_by_policy = defaultdict(int)  # policy_area -> count of bills with cosponsors

    for bill in bills:
        sponsor = bill.get("sponsor", {})
        cosponsors = bill.get("cosponsors", [])
        bill_type = bill.get("bill_type", "")
        policy_area = bill.get("policy_area") or "Unknown"
        chamber = determine_chamber(bill_type)

        # Collect all participants on this bill
        participants = []

        if sponsor and sponsor.get("bioguide_id"):
            sid = sponsor["bioguide_id"]
            participants.append(sid)
            if sid not in legislators:
                legislators[sid] = {
                    "name": sponsor.get("full_name", ""),
                    "party": sponsor.get("party", "?"),
                    "state": sponsor.get("state", "?"),
                    "chamber": chamber,
                    "bills_sponsored": 0,
                    "bills_cosponsored": 0,
                }
            legislators[sid]["bills_sponsored"] += 1

        for c in cosponsors:
            cid = c.get("bioguide_id", "")
            if not cid:
                continue
            participants.append(cid)
            if cid not in legislators:
                legislators[cid] = {
                    "name": c.get("full_name", ""),
                    "party": c.get("party", "?"),
                    "state": c.get("state", "?"),
                    "chamber": chamber,
                    "bills_sponsored": 0,
                    "bills_cosponsored": 0,
                }
            legislators[cid]["bills_cosponsored"] += 1

        # Bipartite degree: only count bills with cosponsors (solo-sponsor
        # bills cannot produce co-occurrence edges and must be excluded from
        # the SDSM null model to avoid inflating mu)
        if cosponsors and sponsor and sponsor.get("bioguide_id"):
            for pid in participants:
                bipartite_degrees[pid] += 1

        # Determine if bill is bipartisan
        parties = set()
        if sponsor and sponsor.get("party"):
            parties.add(sponsor["party"])
        for c in cosponsors:
            if c.get("party"):
                parties.add(c["party"])
        is_bipartisan = ("D" in parties) and ("R" in parties)

        # Update bill participation stats
        for pid in participants:
            bill_participation[pid]["total"] += 1
            bill_participation[pid]["by_policy"][policy_area] += 1
            if is_bipartisan:
                bill_participation[pid]["bipartisan"] += 1

        # Track total bills per policy area (only those with cosponsors)
        if cosponsors and sponsor and sponsor.get("bioguide_id"):
            total_bills_by_policy[policy_area] += 1

    # ── Pass 2: Project into legislator-legislator edges ──
    edge_weights = defaultdict(int)
    edge_policy_areas = defaultdict(lambda: defaultdict(int))

    for bill in bills:
        sponsor = bill.get("sponsor", {})
        cosponsors = bill.get("cosponsors", [])
        if not cosponsors or not sponsor.get("bioguide_id"):
            continue

        policy_area = bill.get("policy_area") or "Unknown"
        s_id = sponsor["bioguide_id"]
        participants = [s_id]
        for c in cosponsors:
            cid = c.get("bioguide_id", "")
            if cid:
                participants.append(cid)

        # Deduplicate participants (a legislator could theoretically appear twice)
        participants = list(dict.fromkeys(participants))

        if len(participants) <= 15:
            # All pairwise combinations
            for a, b in combinations(participants, 2):
                key = tuple(sorted((a, b)))
                edge_weights[key] += 1
                edge_policy_areas[key][policy_area] += 1
        else:
            # Star topology: sponsor to each cosponsor
            for c_id in participants[1:]:
                key = tuple(sorted((s_id, c_id)))
                edge_weights[key] += 1
                edge_policy_areas[key][policy_area] += 1

    # ── Build output ──
    edges = []
    for (a, b), weight in edge_weights.items():
        edges.append({
            "source": a,
            "target": b,
            "weight": weight,
            "policy_areas": dict(edge_policy_areas[(a, b)]),
        })

    # Convert bill_participation defaultdicts to regular dicts
    bp_out = {}
    for pid, stats in bill_participation.items():
        bp_out[pid] = {
            "total": stats["total"],
            "bipartisan": stats["bipartisan"],
            "by_policy": dict(stats["by_policy"]),
        }

    total_bills_with_cosponsors = sum(
        1 for b in bills if b.get("cosponsors") and b.get("sponsor", {}).get("bioguide_id")
    )

    result = {
        "congress": congress,
        "years": years,
        "total_bills": len(bills),
        "total_bills_with_cosponsors": total_bills_with_cosponsors,
        "legislators": legislators,
        "edges": edges,
        "bill_participation": bp_out,
        "bipartite_degrees": dict(bipartite_degrees),
        "total_bills_by_policy": dict(total_bills_by_policy),
    }

    print(f"    Legislators: {len(legislators)}")
    print(f"    Edges: {len(edges)}")
    print(f"    Bills with cosponsors: {total_bills_with_cosponsors}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Stage 2: Project bill data into legislator networks")
    parser.add_argument("--congress", type=int, help="Single congress to process")
    parser.add_argument("--start", type=int, default=DEFAULT_START, help="Start congress (default 108)")
    parser.add_argument("--end", type=int, default=DEFAULT_END, help="End congress (default 119)")
    args = parser.parse_args()

    if args.congress:
        congresses = [args.congress]
    else:
        congresses = list(range(args.start, args.end + 1))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Stage 2: Network Projection")
    print(f"Processing {len(congresses)} congresses: {congresses[0]}-{congresses[-1]}")

    t0 = time.time()

    for congress in congresses:
        result = project_congress(congress)
        if result is None:
            continue

        outpath = OUTPUT_DIR / f"network_{congress}.json"
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"    Written: {outpath}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
