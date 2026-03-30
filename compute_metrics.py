"""
Stage 4: Metrics & Analysis
=============================
Computes centrality, bipartisan scores, ABI, community detection, and
polarization metrics on the SDSM backbone network.

Input:  backbones/backbone_{congress}.json + networks/network_{congress}.json
Output: metrics/metrics_{congress}.json

Usage:
    python compute_metrics.py                        # all 108-119
    python compute_metrics.py --congress 118          # single congress
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    import networkx as nx
    from networkx.algorithms.community import greedy_modularity_communities
except ImportError:
    print("ERROR: 'networkx' is required. Install with: pip install networkx")
    sys.exit(1)

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
BACKBONE_DIR = SCRIPT_DIR / "backbones"
NETWORK_DIR = SCRIPT_DIR / "networks"
OUTPUT_DIR = SCRIPT_DIR / "metrics"
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


def compute_congress_metrics(congress: int) -> dict | None:
    """Compute all metrics for a single congress."""
    backbone_path = BACKBONE_DIR / f"backbone_{congress}.json"
    network_path = NETWORK_DIR / f"network_{congress}.json"

    if not backbone_path.exists():
        print(f"  WARNING: {backbone_path} not found, skipping.")
        return None
    if not network_path.exists():
        print(f"  WARNING: {network_path} not found, skipping.")
        return None

    with open(backbone_path, "r", encoding="utf-8") as f:
        backbone = json.load(f)
    with open(network_path, "r", encoding="utf-8") as f:
        network = json.load(f)

    years = congress_to_years(congress)
    legislators = network["legislators"]
    bill_participation = network.get("bill_participation", {})
    print(f"\n  {ordinal(congress)} Congress ({years})")

    # ── Build positive-edge backbone graph ──
    G_pos = nx.Graph()

    # Add all legislators as nodes
    for bid, info in legislators.items():
        G_pos.add_node(bid, party=info.get("party", "?"))

    positive_edges = []
    negative_edges = []

    for edge in backbone["edges"]:
        src, tgt = edge["source"], edge["target"]
        sign = edge["sign"]
        weight = edge["observed"]

        if sign == "positive":
            G_pos.add_edge(src, tgt, weight=weight)
            positive_edges.append(edge)
        else:
            negative_edges.append(edge)

    print(f"    Positive edges: {len(positive_edges)}")
    print(f"    Negative edges: {len(negative_edges)}")

    # ── Party composition ──
    party_counts = defaultdict(int)
    for info in legislators.values():
        party_counts[info.get("party", "?")] += 1

    total_lawmakers = len(legislators)
    dem_count = party_counts.get("D", 0)
    rep_count = party_counts.get("R", 0)
    other_count = total_lawmakers - dem_count - rep_count

    p_d = dem_count / total_lawmakers if total_lawmakers else 0
    p_r = rep_count / total_lawmakers if total_lawmakers else 0
    p_o = other_count / total_lawmakers if total_lawmakers else 0
    expected_cross_party = 1.0 - (p_d ** 2 + p_r ** 2 + p_o ** 2)

    # ── Centrality on positive backbone ──
    degree_cent = nx.degree_centrality(G_pos)
    betweenness_cent = nx.betweenness_centrality(
        G_pos, weight="weight", k=min(200, G_pos.number_of_nodes())
    )
    try:
        eigenvector_cent = nx.eigenvector_centrality(
            G_pos, max_iter=1000, weight="weight"
        )
    except nx.PowerIterationFailedConvergence:
        eigenvector_cent = {n: 0 for n in G_pos.nodes()}

    # ── Community detection on positive backbone ──
    if G_pos.number_of_edges() > 0:
        communities = list(greedy_modularity_communities(G_pos, weight="weight"))
    else:
        communities = [{n} for n in G_pos.nodes()]

    community_map = {}
    for i, comm in enumerate(communities):
        for node in comm:
            community_map[node] = i

    # ── Party modularity ──
    d_nodes = {n for n in G_pos.nodes() if G_pos.nodes[n].get("party") == "D"}
    r_nodes = {n for n in G_pos.nodes() if G_pos.nodes[n].get("party") == "R"}
    o_nodes = set(G_pos.nodes()) - d_nodes - r_nodes
    party_partition = [s for s in [d_nodes, r_nodes, o_nodes] if s]

    try:
        party_modularity = nx.community.modularity(G_pos, party_partition, weight="weight")
    except Exception:
        party_modularity = 0

    try:
        detected_modularity = nx.community.modularity(G_pos, communities, weight="weight")
    except Exception:
        detected_modularity = 0

    # ── Cross-party edge fraction (on backbone) ──
    cross_party_pos = 0
    total_pos = len(positive_edges)
    for edge in positive_edges:
        pa = legislators.get(edge["source"], {}).get("party", "?")
        pb = legislators.get(edge["target"], {}).get("party", "?")
        if pa != pb and pa != "?" and pb != "?":
            cross_party_pos += 1
    cross_party_edge_frac = cross_party_pos / total_pos if total_pos else 0

    # ── ABI on backbone ──
    # Raw bipartisan rate = cross-party positive edges / total positive edges
    raw_bp_rate = cross_party_pos / total_pos if total_pos else 0
    abi = raw_bp_rate / expected_cross_party if expected_cross_party > 0 else 0

    # ── Polarization metrics (Neal, 2020) ──
    # Denominator: |D| * |R| possible D-R pairs
    possible_dr_pairs = dem_count * rep_count

    # Count D-R pairs with positive ties, negative ties
    dr_positive = 0
    dr_negative = 0
    for edge in backbone["edges"]:
        pa = legislators.get(edge["source"], {}).get("party", "?")
        pb = legislators.get(edge["target"], {}).get("party", "?")
        if sorted([pa, pb]) == ["D", "R"]:
            if edge["sign"] == "positive":
                dr_positive += 1
            elif edge["sign"] == "negative":
                dr_negative += 1

    dr_neutral = possible_dr_pairs - dr_positive - dr_negative
    weak_polarization = dr_neutral / possible_dr_pairs if possible_dr_pairs else 0
    strong_polarization = dr_negative / possible_dr_pairs if possible_dr_pairs else 0

    print(f"    ABI: {abi:.4f}")
    print(f"    Weak polarization: {weak_polarization:.4f}")
    print(f"    Strong polarization: {strong_polarization:.4f}")
    print(f"    Communities: {len(communities)}")

    # ── Per-lawmaker bipartisan score ──
    # Proportion of positive backbone ties that are cross-party
    node_positive_ties = defaultdict(int)
    node_negative_ties = defaultdict(int)
    node_cross_party_positive = defaultdict(int)

    for edge in positive_edges:
        node_positive_ties[edge["source"]] += 1
        node_positive_ties[edge["target"]] += 1
        pa = legislators.get(edge["source"], {}).get("party", "?")
        pb = legislators.get(edge["target"], {}).get("party", "?")
        if pa != pb and pa != "?" and pb != "?":
            node_cross_party_positive[edge["source"]] += 1
            node_cross_party_positive[edge["target"]] += 1

    for edge in negative_edges:
        node_negative_ties[edge["source"]] += 1
        node_negative_ties[edge["target"]] += 1

    # ── Build nodes output ──
    nodes_data = []
    for bid, info in legislators.items():
        pos_ties = node_positive_ties.get(bid, 0)
        bp_score = (node_cross_party_positive.get(bid, 0) / pos_ties
                    if pos_ties > 0 else 0)

        bp_stats = bill_participation.get(bid, {})

        nodes_data.append({
            "id": bid,
            "name": info.get("name", bid),
            "party": info.get("party", "?"),
            "state": info.get("state", "?"),
            "chamber": info.get("chamber", "?"),
            "degree_centrality": round(degree_cent.get(bid, 0), 4),
            "betweenness": round(betweenness_cent.get(bid, 0), 6),
            "eigenvector": round(eigenvector_cent.get(bid, 0), 4),
            "bipartisan_score": round(bp_score, 4),
            "community": community_map.get(bid, -1),
            "positive_ties": pos_ties,
            "negative_ties": node_negative_ties.get(bid, 0),
            "cross_party_positive": node_cross_party_positive.get(bid, 0),
            "bills_sponsored": info.get("bills_sponsored", 0),
            "bills_cosponsored": info.get("bills_cosponsored", 0),
            "total_bills": bp_stats.get("total", 0),
            "bipartisan_bills": bp_stats.get("bipartisan", 0),
            "policy_areas": bp_stats.get("by_policy", {}),
        })

    # ── Build edges output ──
    edges_data = []
    for edge in backbone["edges"]:
        pa = legislators.get(edge["source"], {}).get("party", "?")
        pb = legislators.get(edge["target"], {}).get("party", "?")
        cross = pa != pb and pa != "?" and pb != "?"
        edges_data.append({
            "source": edge["source"],
            "target": edge["target"],
            "sign": edge["sign"],
            "weight": edge["observed"],
            "expected": edge["expected"],
            "z_score": edge.get("z_score", 0),
            "p_value": edge.get("p_value", 0),
            "cross_party": cross,
            "policy_areas": edge.get("policy_areas", {}),
        })

    # ── Policy area stats from policy backbones ──
    policy_area_stats = {}
    for area, pb in backbone.get("policy_backbones", {}).items():
        area_pos = sum(1 for e in pb["edges"] if e["sign"] == "positive")
        area_neg = sum(1 for e in pb["edges"] if e["sign"] == "negative")
        area_cross = 0
        for e in pb["edges"]:
            if e["sign"] == "positive":
                pa = legislators.get(e["source"], {}).get("party", "?")
                ppb = legislators.get(e["target"], {}).get("party", "?")
                if pa != ppb and pa != "?" and ppb != "?":
                    area_cross += 1
        area_raw_bp = area_cross / area_pos if area_pos > 0 else 0
        area_abi = area_raw_bp / expected_cross_party if expected_cross_party > 0 else 0

        policy_area_stats[area] = {
            "abi": round(area_abi, 4),
            "positive_edges": area_pos,
            "negative_edges": area_neg,
            "cross_party_positive": area_cross,
            "total_bills": pb.get("total_bills", 0),
        }

    # ── Community summary ──
    comm_summary = defaultdict(lambda: {"size": 0, "D": 0, "R": 0, "I": 0, "Other": 0})
    for n in nodes_data:
        c = n["community"]
        comm_summary[c]["size"] += 1
        p = n["party"]
        if p in ("D", "R", "I"):
            comm_summary[c][p] += 1
        else:
            comm_summary[c]["Other"] += 1

    return {
        "congress": congress,
        "years": years,
        "summary": {
            "abi": round(abi, 4),
            "raw_bipartisan_rate": round(raw_bp_rate, 4),
            "expected_cross_party_rate": round(expected_cross_party, 4),
            "party_modularity": round(party_modularity, 4),
            "detected_modularity": round(detected_modularity, 4),
            "weak_polarization": round(weak_polarization, 4),
            "strong_polarization": round(strong_polarization, 4),
            "cross_party_edge_fraction": round(cross_party_edge_frac, 4),
            "n_communities": len(communities),
            "positive_edges": len(positive_edges),
            "negative_edges": len(negative_edges),
            "density": round(nx.density(G_pos), 6) if G_pos.number_of_nodes() > 1 else 0,
        },
        "party_composition": {
            "D": dem_count, "R": rep_count, "Other": other_count,
            "total": total_lawmakers,
        },
        "nodes": nodes_data,
        "edges": edges_data,
        "policy_area_stats": policy_area_stats,
        "community_summary": {str(k): dict(v) for k, v in
                              sorted(comm_summary.items(),
                                     key=lambda x: x[1]["size"], reverse=True)},
    }


def main():
    parser = argparse.ArgumentParser(
        description="Stage 4: Compute metrics on SDSM backbone"
    )
    parser.add_argument("--congress", type=int, help="Single congress")
    parser.add_argument("--start", type=int, default=DEFAULT_START)
    parser.add_argument("--end", type=int, default=DEFAULT_END)
    args = parser.parse_args()

    if args.congress:
        congresses = [args.congress]
    else:
        congresses = list(range(args.start, args.end + 1))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Stage 4: Metrics & Analysis")
    print(f"Processing {len(congresses)} congresses")

    t0 = time.time()

    for congress in congresses:
        result = compute_congress_metrics(congress)
        if result is None:
            continue

        outpath = OUTPUT_DIR / f"metrics_{congress}.json"
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"    Written: {outpath}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
