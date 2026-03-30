"""
Stage 3: SDSM Backbone Extraction
===================================
Applies the Stochastic Degree Sequence Model (Neal, 2014) to extract a
signed backbone from the bipartite projection. Each legislator pair is
tested against a null model that preserves degree sequences.

Input:  networks/network_{congress}.json
Output: backbones/backbone_{congress}.json

Usage:
    python build_backbone.py                        # all 108-119
    python build_backbone.py --congress 118          # single congress
    python build_backbone.py --start 110 --end 119   # range
    python build_backbone.py --alpha 0.01            # stricter significance
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from scipy.stats import norm

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
NETWORK_DIR = SCRIPT_DIR / "networks"
OUTPUT_DIR = SCRIPT_DIR / "backbones"
DEFAULT_START = 108
DEFAULT_END = 119
DEFAULT_ALPHA = 0.05
MIN_POLICY_BILLS = 50  # minimum bills in a policy area for decomposition


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def sdsm_test(edges: list, bipartite_degrees: dict, total_bills: int,
              alpha: float) -> tuple[list, dict]:
    """
    Run SDSM significance test on all edges.

    For each edge (i, j) with observed weight w_ij:
      mu = d_i * d_j / B
      sigma^2 = mu * (1 - mu / B)
      z = (w_ij - mu) / sigma

    Then classify using Benjamini-Hochberg FDR correction at level alpha.

    Returns list of dicts with observed, expected, p_value, sign fields.
    """
    if total_bills == 0 or not edges:
        return []

    B = total_bills
    n_edges = len(edges)

    # Compute z-scores and p-values for all edges
    results = []
    p_upper_list = []
    p_lower_list = []

    for edge in edges:
        src = edge["source"]
        tgt = edge["target"]
        w = edge["weight"]

        d_i = bipartite_degrees.get(src, 0)
        d_j = bipartite_degrees.get(tgt, 0)

        if d_i == 0 or d_j == 0:
            results.append(None)
            p_upper_list.append(1.0)
            p_lower_list.append(1.0)
            continue

        mu = (d_i * d_j) / B
        sigma_sq = mu * (1.0 - mu / B)

        if sigma_sq <= 0:
            results.append(None)
            p_upper_list.append(1.0)
            p_lower_list.append(1.0)
            continue

        sigma = math.sqrt(sigma_sq)
        z = (w - mu) / sigma

        p_upper = 1.0 - norm.cdf(z)  # tests for MORE cosponsorship
        p_lower = norm.cdf(z)          # tests for LESS cosponsorship

        results.append({
            "source": src,
            "target": tgt,
            "observed": w,
            "expected": round(mu, 4),
            "z_score": round(z, 4),
            "p_upper": p_upper,
            "p_lower": p_lower,
            "policy_areas": edge.get("policy_areas", {}),
        })
        p_upper_list.append(p_upper)
        p_lower_list.append(p_lower)

    # Benjamini-Hochberg FDR correction
    # Apply separately for upper and lower tails
    upper_reject = _bh_correction(p_upper_list, alpha)
    lower_reject = _bh_correction(p_lower_list, alpha)

    # Classify edges
    backbone_edges = []
    stats = {"total_pairs_tested": n_edges, "positive_edges": 0,
             "negative_edges": 0, "neutral_discarded": 0}

    for i, res in enumerate(results):
        if res is None:
            stats["neutral_discarded"] += 1
            continue

        if upper_reject[i]:
            res["sign"] = "positive"
            res["p_value"] = round(res["p_upper"], 6)
            stats["positive_edges"] += 1
            backbone_edges.append(_clean_edge(res))
        elif lower_reject[i]:
            res["sign"] = "negative"
            res["p_value"] = round(res["p_lower"], 6)
            stats["negative_edges"] += 1
            backbone_edges.append(_clean_edge(res))
        else:
            stats["neutral_discarded"] += 1

    return backbone_edges, stats


def _bh_correction(p_values: list, alpha: float) -> list:
    """
    Benjamini-Hochberg FDR correction.
    Returns a boolean list indicating which hypotheses are rejected.
    """
    n = len(p_values)
    if n == 0:
        return []

    # Sort p-values, keeping track of original indices
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    reject = [False] * n

    # Find the largest k such that p_(k) <= (k/n) * alpha
    max_k = -1
    for rank, (orig_idx, p) in enumerate(indexed, 1):
        threshold = (rank / n) * alpha
        if p <= threshold:
            max_k = rank

    # Reject all hypotheses with rank <= max_k
    if max_k > 0:
        for rank, (orig_idx, p) in enumerate(indexed, 1):
            if rank <= max_k:
                reject[orig_idx] = True

    return reject


def _clean_edge(res: dict) -> dict:
    """Remove intermediate fields from edge result for output."""
    return {
        "source": res["source"],
        "target": res["target"],
        "observed": res["observed"],
        "expected": res["expected"],
        "z_score": res["z_score"],
        "p_value": res["p_value"],
        "sign": res["sign"],
        "policy_areas": res["policy_areas"],
    }


def build_policy_backbones(network: dict, alpha: float) -> dict:
    """
    Build per-policy-area backbones.

    For each policy area with >= MIN_POLICY_BILLS bills:
    1. Restrict to bills in that area
    2. Recompute bipartite degrees within the area (from bill_participation.by_policy)
    3. Recompute edge weights within the area (from edge.policy_areas)
    4. Run SDSM test

    Uses total_bills_by_policy from the network JSON (computed in Stage 2),
    so this stage only depends on its documented input.
    """
    area_bipartite_degrees = {}  # {area: {bioguide_id: count}}
    area_edge_weights = {}       # {area: {(src, tgt): weight}}

    # Use edge-level policy_areas to build per-area edge lists
    for edge in network["edges"]:
        pa = edge.get("policy_areas", {})
        for area, count in pa.items():
            if area not in area_edge_weights:
                area_edge_weights[area] = {}
            key = (edge["source"], edge["target"])
            area_edge_weights[area][key] = count

    # Use bill_participation by_policy to build per-area bipartite degrees
    bp = network.get("bill_participation", {})
    for pid, stats in bp.items():
        by_policy = stats.get("by_policy", {})
        for area, count in by_policy.items():
            if area not in area_bipartite_degrees:
                area_bipartite_degrees[area] = {}
            area_bipartite_degrees[area][pid] = count

    # Get total bills per policy area from network output (computed in Stage 2)
    area_total_bills = network.get("total_bills_by_policy", {})

    policy_backbones = {}
    for area in sorted(area_edge_weights.keys()):
        B_A = area_total_bills.get(area, 0)
        if B_A < MIN_POLICY_BILLS:
            continue

        degrees_A = area_bipartite_degrees.get(area, {})
        edges_A = [
            {"source": src, "target": tgt, "weight": w, "policy_areas": {area: w}}
            for (src, tgt), w in area_edge_weights[area].items()
        ]

        if not edges_A:
            continue

        backbone_edges, stats = sdsm_test(edges_A, degrees_A, B_A, alpha)
        policy_backbones[area] = {
            "total_bills": B_A,
            "edges": backbone_edges,
            "stats": stats,
        }

    return policy_backbones


def extract_backbone(congress: int, alpha: float) -> dict | None:
    """Extract SDSM backbone for a single congress."""
    network_path = NETWORK_DIR / f"network_{congress}.json"
    if not network_path.exists():
        print(f"  WARNING: {network_path} not found, skipping.")
        return None

    with open(network_path, "r", encoding="utf-8") as f:
        network = json.load(f)

    years = network["years"]
    print(f"\n  {ordinal(congress)} Congress ({years})")
    print(f"    Edges to test: {len(network['edges'])}")
    print(f"    Legislators: {len(network['legislators'])}")

    # Run overall SDSM
    bipartite_degrees = network["bipartite_degrees"]
    total_bills = network.get("total_bills_with_cosponsors", network["total_bills"])

    backbone_edges, stats = sdsm_test(
        network["edges"], bipartite_degrees, total_bills, alpha
    )

    print(f"    Positive edges: {stats['positive_edges']}")
    print(f"    Negative edges: {stats['negative_edges']}")
    print(f"    Neutral (discarded): {stats['neutral_discarded']}")

    # Run policy-area decomposition
    print(f"    Computing policy-area backbones...")
    policy_backbones = build_policy_backbones(network, alpha)
    print(f"    Policy areas with backbone: {len(policy_backbones)}")

    return {
        "congress": congress,
        "years": years,
        "alpha": alpha,
        "method": "SDSM_normal_approximation",
        "fdr_correction": "Benjamini-Hochberg",
        "edges": backbone_edges,
        "stats": stats,
        "policy_backbones": policy_backbones,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Stage 3: SDSM backbone extraction"
    )
    parser.add_argument("--congress", type=int, help="Single congress")
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help="Significance level (default 0.05)")
    args = parser.parse_args()

    if args.congress:
        congresses = [args.congress]
    else:
        congresses = list(range(args.start, args.end + 1))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Stage 3: SDSM Backbone Extraction (alpha={args.alpha})")
    print(f"Processing {len(congresses)} congresses")

    t0 = time.time()

    for congress in congresses:
        result = extract_backbone(congress, args.alpha)
        if result is None:
            continue

        outpath = OUTPUT_DIR / f"backbone_{congress}.json"
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"    Written: {outpath}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
