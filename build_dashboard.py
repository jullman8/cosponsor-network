"""
Stage 5 — Bipartisanship Dashboard (reads pre-computed metrics JSON)
====================================================================
Reads metrics/metrics_{108..119}.json produced by Stages 1-4 and generates
a single self-contained HTML dashboard: bipartisanship_dashboard.html.

No networkx or heavy computation — just JSON loading and HTML assembly.

Usage:
    python build_dashboard.py
"""

import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
METRICS_DIR = SCRIPT_DIR / "metrics"
OUTPUT_FILE = SCRIPT_DIR / "bipartisanship_dashboard.html"

CONGRESSES = list(range(108, 120))


def congress_to_years(congress: int) -> str:
    start_year = 1789 + (congress - 1) * 2
    return f"{start_year}-{start_year + 2}"


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def load_metrics(congress: int) -> dict | None:
    """Load a pre-computed metrics JSON for one congress."""
    filepath = METRICS_DIR / f"metrics_{congress}.json"
    if not filepath.exists():
        print(f"  WARNING: {filepath} not found, skipping.")
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def assemble_data(all_metrics: list[dict]) -> tuple:
    """
    Transform loaded metrics into the data structures the HTML template needs:
      timeseries_data, policy_matrix, detail_data, congress_list
    """
    timeseries_data = []
    for m in all_metrics:
        s = m["summary"]
        pc = m["party_composition"]

        # Compute per-node party averages for top bipartisan lawmakers
        top_bp = []
        for n in m["nodes"]:
            if n["total_bills"] >= 10:
                top_bp.append({
                    "name": n["name"],
                    "party": n["party"],
                    "state": n["state"],
                    "bipartisan_score": round(n["bipartisan_score"], 4),
                    "bipartisan_bills": n["bipartisan_bills"],
                    "total_bills": n["total_bills"],
                })
        top_bp.sort(key=lambda x: x["bipartisan_score"], reverse=True)

        timeseries_data.append({
            "congress": m["congress"],
            "years": m["years"],
            "party_composition": {
                "D": pc.get("D", 0),
                "R": pc.get("R", 0),
                "Other": pc.get("Other", 0),
                "total": pc.get("total", 0),
            },
            "bill_stats": {
                "total_bills": sum(n["total_bills"] for n in m["nodes"]) // max(1, len(m["nodes"])) * len(m["nodes"]),
                "bipartisan_bills": sum(n["bipartisan_bills"] for n in m["nodes"]) // max(1, len(m["nodes"])) * len(m["nodes"]),
                "raw_bipartisan_rate": round(s["raw_bipartisan_rate"], 4),
                "expected_cross_party_rate": round(s["expected_cross_party_rate"], 4),
                "adjusted_bipartisan_index": round(s["abi"], 4),
            },
            "network_stats": {
                "nodes": len(m["nodes"]),
                "edges": len(m["edges"]),
                "density": round(s["density"], 6),
                "cross_party_edge_fraction": round(s["cross_party_edge_fraction"], 4),
                "party_modularity": round(s["party_modularity"], 4),
                "detected_modularity": round(s["detected_modularity"], 4),
                "n_communities": s["n_communities"],
                "positive_edges": s["positive_edges"],
                "negative_edges": s["negative_edges"],
            },
            "polarization": {
                "weak": round(s["weak_polarization"], 4),
                "strong": round(s["strong_polarization"], 4),
            },
            "top_bipartisan_lawmakers": top_bp[:20],
        })

    # Policy area ABI matrix across congresses
    all_areas = set()
    for m in all_metrics:
        all_areas.update(m["policy_area_stats"].keys())
    all_areas = sorted(all_areas)

    policy_matrix = {}
    for area in all_areas:
        policy_matrix[area] = {
            "abi": [],
            "raw_rate": [],
            "bill_count": [],
            "congresses": [],
        }
        for m in all_metrics:
            stats = m["policy_area_stats"].get(area)
            if stats and stats["total_bills"] >= 5:
                policy_matrix[area]["abi"].append(round(stats["abi"], 4))
                # raw_rate derived from abi * expected
                raw = round(stats["abi"] * m["summary"]["expected_cross_party_rate"], 4)
                policy_matrix[area]["raw_rate"].append(raw)
                policy_matrix[area]["bill_count"].append(stats["total_bills"])
                policy_matrix[area]["congresses"].append(m["congress"])
            else:
                policy_matrix[area]["abi"].append(None)
                policy_matrix[area]["raw_rate"].append(None)
                policy_matrix[area]["bill_count"].append(0)
                policy_matrix[area]["congresses"].append(m["congress"])

    # Per-congress detail data
    detail_data = {}
    for m in all_metrics:
        nodes = m["nodes"]
        edges = m["edges"]

        # Party summary (avg bipartisan score per party)
        from collections import defaultdict
        party_avg = defaultdict(lambda: {"count": 0, "total_bp": 0})
        for n in nodes:
            p = n["party"]
            party_avg[p]["count"] += 1
            party_avg[p]["total_bp"] += n["bipartisan_score"]
        party_summary = {p: round(v["total_bp"] / v["count"], 4) if v["count"] else 0
                         for p, v in party_avg.items()}

        # State summary
        state_stats = defaultdict(lambda: {"count": 0, "total_bp": 0})
        for n in nodes:
            s = n["state"]
            state_stats[s]["count"] += 1
            state_stats[s]["total_bp"] += n["bipartisan_score"]
        state_summary = {s: {"avg_bp": round(v["total_bp"] / v["count"], 4), "count": v["count"]}
                         for s, v in state_stats.items()}

        # Sorted leaderboards
        top_bipartisan = sorted(nodes, key=lambda x: x["bipartisan_score"], reverse=True)
        top_betweenness = sorted(nodes, key=lambda x: x["betweenness"], reverse=True)

        # Community summary with top members
        comm_summary = defaultdict(lambda: {"size": 0, "D": 0, "R": 0, "I": 0, "Other": 0})
        for n in nodes:
            c = n["community"]
            comm_summary[c]["size"] += 1
            p = n["party"]
            if p in comm_summary[c]:
                comm_summary[c][p] += 1
        comm_summary = dict(sorted(comm_summary.items(), key=lambda x: x[1]["size"], reverse=True))

        # Use pre-computed community_summary if available (has accurate counts)
        if m.get("community_summary"):
            comm_summary = {}
            for cid, cs in m["community_summary"].items():
                comm_summary[cid] = {
                    "size": cs.get("size", 0),
                    "D": cs.get("D", 0),
                    "R": cs.get("R", 0),
                    "I": cs.get("I", 0),
                    "Other": cs.get("Other", 0),
                }
            comm_summary = dict(sorted(comm_summary.items(),
                                       key=lambda x: x[1]["size"], reverse=True))

        comm_top_members = {}
        for cid in comm_summary:
            members = [n for n in nodes if str(n["community"]) == str(cid)]
            members.sort(key=lambda x: x["betweenness"], reverse=True)
            comm_top_members[str(cid)] = [{"name": mm["name"], "party": mm["party"]} for mm in members[:3]]

        # Policy summary for bar chart
        pa_stats = m.get("policy_area_stats", {})
        policy_summary = {}
        for area, st in pa_stats.items():
            total = st["total_bills"]
            # cross_party_positive / positive_edges as a proxy for bipartisan pct
            pos = st.get("positive_edges", 0)
            cp = st.get("cross_party_positive", 0)
            pct = round(cp / pos * 100, 1) if pos > 0 else 0
            policy_summary[area] = {
                "total": total,
                "bipartisan": cp,
                "pct": round(st["abi"] * 100, 1),  # ABI*100 for display
            }

        # Policy areas list
        policy_areas_list = sorted(pa_stats.keys())

        detail_data[m["congress"]] = {
            "nodes": nodes,
            "edges": edges,
            "party_summary": party_summary,
            "state_summary": state_summary,
            "top_bipartisan": top_bipartisan,
            "top_betweenness": top_betweenness,
            "comm_summary": comm_summary,
            "comm_top_members": comm_top_members,
            "policy_summary": policy_summary,
            "policy_areas_list": policy_areas_list,
            "summary": m["summary"],
        }

    # Congress list for dropdown
    congress_list = []
    for m in all_metrics:
        d = detail_data[m["congress"]]
        congress_list.append({
            "num": m["congress"],
            "years": m["years"],
            "label": f"{ordinal(m['congress'])} ({m['years']})",
            "total_bills": m["summary"].get("positive_edges", 0) + m["summary"].get("negative_edges", 0),
            "bipartisan_bills": m["summary"].get("positive_edges", 0),
            "node_count": len(d["nodes"]),
            "edge_count": len(d["edges"]),
        })

    return timeseries_data, policy_matrix, detail_data, congress_list


def generate_html(timeseries_data, policy_matrix, detail_data, congress_list) -> str:
    """Generate the unified HTML dashboard."""
    timeseries_json = json.dumps(timeseries_data)
    policy_matrix_json = json.dumps(policy_matrix)
    detail_json = json.dumps(detail_data)
    congress_list_json = json.dumps(congress_list)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Congressional Bipartisanship Dashboard</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e0e0e0; }}
h1 {{ text-align: center; padding: 18px; font-size: 1.5rem; color: #fff; background: linear-gradient(135deg, #1a1a2e, #16213e); }}
h1 span {{ font-weight: 300; font-size: 0.9rem; color: #8892b0; display: block; margin-top: 4px; }}

/* Tab navigation */
.main-tabs {{ display: flex; justify-content: center; gap: 0; background: #12141d; border-bottom: 2px solid #2a2d3a; }}
.main-tab {{ background: transparent; color: #8892b0; border: none; padding: 14px 32px; font-size: 1rem; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent; transition: all 0.2s; }}
.main-tab:hover {{ color: #ccd6f6; background: #1a1d29; }}
.main-tab.active {{ color: #ffd93d; border-bottom-color: #ffd93d; }}
.main-tab-content {{ display: none; }}
.main-tab-content.active {{ display: block; }}

.dashboard {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px; max-width: 1800px; margin: 0 auto; }}
.full-width {{ grid-column: 1 / -1; }}
.panel {{ background: #1a1d29; border-radius: 10px; padding: 18px; border: 1px solid #2a2d3a; }}
.panel h2 {{ font-size: 1.05rem; margin-bottom: 4px; color: #ccd6f6; border-bottom: 1px solid #2a2d3a; padding-bottom: 8px; }}
.panel .subtitle {{ font-size: 0.82rem; color: #8892b0; margin-bottom: 12px; }}

/* Network container */
#network-container {{ position: relative; }}
#network {{ width: 100%; height: 650px; background: #12141d; border-radius: 8px; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; align-items: center; }}
.controls label {{ font-size: 0.82rem; color: #8892b0; }}
.controls select, .controls input[type=range] {{ background: #2a2d3a; color: #e0e0e0; border: 1px solid #3a3d4a; border-radius: 4px; padding: 4px 8px; font-size: 0.82rem; }}
.controls input[type=range] {{ width: 120px; }}
.controls button {{ background: #2a2d3a; color: #e0e0e0; border: 1px solid #3a3d4a; border-radius: 4px; padding: 4px 12px; font-size: 0.82rem; cursor: pointer; }}
.controls button:hover {{ background: #3a3d4a; }}
.controls button.active {{ background: #4a6fa5; border-color: #6a8fc5; }}

/* Tables */
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
th {{ text-align: left; padding: 6px 8px; border-bottom: 2px solid #2a2d3a; color: #8892b0; font-weight: 600; }}
td {{ padding: 5px 8px; border-bottom: 1px solid #1e2130; }}
tr:hover td {{ background: #1e2130; }}
.party-D {{ color: #5b9bf5; }}
.party-R {{ color: #f56565; }}
.party-I {{ color: #b794f6; }}
.bar-fill {{ height: 14px; border-radius: 3px; min-width: 2px; }}

/* Tooltip */
.tooltip {{ position: absolute; background: #1e2130; border: 1px solid #4a6fa5; border-radius: 6px; padding: 10px 14px; font-size: 0.8rem; pointer-events: none; opacity: 0; transition: opacity 0.15s; z-index: 100; max-width: 300px; }}
.tooltip .name {{ font-weight: 700; font-size: 0.9rem; margin-bottom: 4px; }}
.tooltip .stat {{ color: #8892b0; }}

/* Search */
#search {{ background: #2a2d3a; color: #e0e0e0; border: 1px solid #3a3d4a; border-radius: 4px; padding: 5px 10px; font-size: 0.82rem; width: 200px; }}
#search::placeholder {{ color: #555; }}

/* Legend */
.legend {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 0.8rem; margin-top: 8px; }}
.legend-item {{ display: flex; align-items: center; gap: 4px; }}
.legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
.legend-line {{ width: 24px; height: 0; border-top: 2px solid; }}

/* Charts */
.chart {{ width: 100%; }}
.chart-container {{ height: 320px; }}

/* Inner tabs */
.tab-bar {{ display: flex; gap: 4px; margin-bottom: 10px; }}
.tab-btn {{ background: #2a2d3a; color: #8892b0; border: none; border-radius: 4px 4px 0 0; padding: 6px 14px; cursor: pointer; font-size: 0.82rem; }}
.tab-btn.active {{ background: #4a6fa5; color: #fff; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

/* Metric cards */
.metric-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 16px; }}
.metric-card {{ background: #12141d; border-radius: 8px; padding: 14px; text-align: center; }}
.metric-card .value {{ font-size: 1.8rem; font-weight: 700; color: #ccd6f6; }}
.metric-card .label {{ font-size: 0.78rem; color: #8892b0; margin-top: 4px; }}

/* Congress selector */
.congress-selector {{ display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }}
.congress-selector select {{ background: #2a2d3a; color: #e0e0e0; border: 1px solid #3a3d4a; border-radius: 6px; padding: 8px 14px; font-size: 0.95rem; cursor: pointer; }}
.congress-info {{ font-size: 0.85rem; color: #8892b0; }}

/* Loading overlay */
.loading-overlay {{ display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(15,17,23,0.8); z-index: 50; justify-content: center; align-items: center; border-radius: 10px; }}
.loading-overlay.active {{ display: flex; }}
.loading-spinner {{ color: #ffd93d; font-size: 1rem; }}

/* Policy area dropdown */
.policy-dropdown {{ background: #2a2d3a; color: #e0e0e0; border: 1px solid #3a3d4a; border-radius: 4px; padding: 6px 10px; font-size: 0.85rem; max-width: 300px; }}

/* Export button */
.export-btn {{ background: #2a2d3a; color: #8892b0; border: 1px solid #3a3d4a; border-radius: 4px; padding: 3px 10px; font-size: 0.75rem; cursor: pointer; float: right; margin-top: -2px; }}
.export-btn:hover {{ background: #3a3d4a; color: #ccd6f6; }}

/* Detail panel */
.detail-panel {{
  display: none; position: absolute; top: 0; right: 0; width: 360px; height: 100%;
  background: #1a1d29; border-left: 2px solid #4a6fa5; z-index: 60;
  overflow-y: auto; padding: 14px; border-radius: 0 10px 10px 0;
  box-shadow: -4px 0 20px rgba(0,0,0,0.5);
}}
.detail-panel.open {{ display: block; }}
.detail-panel-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
.detail-panel-header h3 {{ font-size: 1rem; color: #ccd6f6; margin: 0; }}
.detail-panel-header button {{ background: none; border: none; color: #8892b0; font-size: 1.4rem; cursor: pointer; padding: 0 4px; }}
.detail-panel-header button:hover {{ color: #fff; }}
.detail-meta {{ font-size: 0.82rem; color: #8892b0; margin-bottom: 8px; }}
.detail-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 8px; }}
.detail-stat {{ background: #12141d; border-radius: 6px; padding: 8px; text-align: center; }}
.detail-stat .val {{ font-size: 1.1rem; font-weight: 700; color: #ccd6f6; }}
.detail-stat .lbl {{ font-size: 0.7rem; color: #8892b0; }}
.colleague-row {{ display: flex; align-items: center; padding: 5px 0; border-bottom: 1px solid #1e2130; font-size: 0.8rem; }}
.colleague-row .rank {{ width: 24px; color: #555; text-align: center; }}
.colleague-row .cname {{ flex: 1; }}
.colleague-row .ccount {{ width: 50px; text-align: right; color: #ccd6f6; font-weight: 600; }}
.colleague-bar {{ height: 3px; background: #4a6fa5; border-radius: 2px; margin-top: 2px; }}

@media (max-width: 900px) {{
  .dashboard {{ grid-template-columns: 1fr; }}
  .detail-panel {{ width: 100%; position: relative; border-left: none; border-top: 2px solid #4a6fa5; }}
}}
</style>
</head>
<body>

<h1>Congressional Bipartisanship Dashboard
<span>108th&ndash;119th Congress (2003&ndash;2027) &mdash; Backbone Cosponsor Network Analysis</span>
</h1>

<div class="main-tabs">
  <button class="main-tab active" data-tab="trends-tab">Trends Over Time</button>
  <button class="main-tab" data-tab="detail-tab">Per-Congress Detail</button>
</div>

<!-- ═══════════════ TAB 1: TRENDS OVER TIME ═══════════════ -->
<div class="main-tab-content active" id="trends-tab">
<div class="dashboard">

<!-- Summary Cards -->
<div class="panel full-width" id="summary-cards"></div>

<!-- Main ABI Chart with policy area dropdown -->
<div class="panel full-width">
  <h2>Adjusted Bipartisan Index (ABI) Over Time <button class="export-btn" onclick="exportPlotly('abiChart','ABI_Over_Time')">Export PNG</button></h2>
  <p class="subtitle">Measures cross-party cooperation on the SDSM backbone, adjusted for party composition. ABI = (observed cross-party cooperation rate) / (rate expected if party mixing were random). ABI = 1.0 means cooperation matches random chance; below 1.0 means less cooperation than expected. Use the dropdown to view ABI for a specific policy area.</p>
  <div style="margin-bottom:10px;">
    <label style="font-size:0.85rem;color:#8892b0;">Policy Area:
      <select id="policyAreaSelect" class="policy-dropdown">
        <option value="overall">Overall (All Policy Areas)</option>
      </select>
    </label>
  </div>
  <div id="abiChart" class="chart" style="height:400px;"></div>
</div>

<!-- Polarization Trends -->
<div class="panel full-width">
  <h2>Polarization Trends <button class="export-btn" onclick="exportPlotly('polarizationChart','Polarization_Trends')">Export PNG</button></h2>
  <p class="subtitle">Two distinct forms of polarization, measured across all possible Democrat-Republican legislator pairs. <strong>Weak polarization</strong>: the fraction of D-R pairs with no statistically significant cosponsorship tie &mdash; they simply don't interact. <strong>Strong polarization</strong>: the fraction of D-R pairs who cosponsor together significantly <em>less</em> than their activity levels would predict &mdash; active avoidance. Rising weak polarization means parties are sorting into separate worlds; rising strong polarization means they are actively repelling.</p>
  <div id="polarizationChart" class="chart" style="height:400px;"></div>
</div>

<!-- Raw vs Expected -->
<div class="panel">
  <h2>Raw vs Expected Bipartisan Rates <button class="export-btn" onclick="exportPlotly('rawExpChart','Raw_vs_Expected')">Export PNG</button></h2>
  <p class="subtitle">The two components of the ABI calculation. <strong>Raw rate</strong>: the actual fraction of backbone cooperation ties that cross party lines. <strong>Expected rate</strong>: the fraction that would cross party lines if cooperation were random (determined by party balance). When the raw rate falls below expected, ABI drops below 1.0. This chart shows whether ABI changes are driven by shifts in actual cooperation or shifts in party composition.</p>
  <div id="rawExpChart" class="chart" style="height:350px;"></div>
</div>

<!-- Party Composition -->
<div class="panel">
  <h2>Party Composition Over Time <button class="export-btn" onclick="exportPlotly('partyCompChart','Party_Composition')">Export PNG</button></h2>
  <p class="subtitle">Number of active legislators by party in each Congress. This context is essential for interpreting the charts above &mdash; party balance affects the expected cross-party rate, which in turn affects ABI and polarization measures.</p>
  <div id="partyCompChart" class="chart" style="height:350px;"></div>
</div>

<!-- Policy Area Heatmap -->
<div class="panel full-width">
  <h2>Bipartisanship by Policy Area Over Time <button class="export-btn" onclick="exportPlotly('policyHeatmap','Policy_Area_Heatmap')">Export PNG</button></h2>
  <p class="subtitle">ABI computed separately for each policy area (e.g., Health, Defense, Education) using per-area SDSM backbone extraction. Brighter colors = more bipartisan cooperation than expected in that domain. Gray = fewer than 50 bills in that area for that Congress (insufficient data for reliable statistical testing). Reveals which policy domains unite or divide Congress.</p>
  <div id="policyHeatmap" class="chart" style="height:600px;"></div>
</div>

<!-- Per-congress top lawmakers -->
<div class="panel full-width">
  <h2>Most Bipartisan Lawmakers by Congress</h2>
  <p class="subtitle">Top 5 lawmakers per Congress ranked by bipartisan score (minimum 10 bills). Score = fraction of a lawmaker's statistically significant cooperation ties that cross party lines. A score of 50% means half their backbone connections are with the opposing party.</p>
  <div id="topLawmakers"></div>
</div>

</div><!-- end trends dashboard -->
</div><!-- end trends tab -->

<!-- ═══════════════ TAB 2: PER-CONGRESS DETAIL ═══════════════ -->
<div class="main-tab-content" id="detail-tab">
<div class="dashboard">

<!-- Congress selector -->
<div class="panel full-width">
  <div class="congress-selector">
    <label style="font-size:0.95rem;color:#ccd6f6;font-weight:600;">Select Congress:</label>
    <select id="congressSelect"></select>
    <span class="congress-info" id="congressInfo"></span>
  </div>
</div>

<!-- Network Graph -->
<div class="panel full-width" id="network-container" style="position:relative;">
  <h2>Backbone Cosponsor Network <button class="export-btn" onclick="exportSvg('network','Cosponsor_Network')">Export PNG</button></h2>
  <p class="subtitle" style="margin-bottom:8px;">Edges represent statistically significant cooperation (SDSM backbone, p &lt; 0.05 after FDR correction).</p>
  <div class="controls">
    <label>Show: <select id="edgeTypeFilter"><option value="positive" selected>All cooperation</option><option value="cross_party">Cross-party only</option></select></label>
    <label>Party: <select id="partyFilter"><option value="all">All</option><option value="D">Democrat</option><option value="R">Republican</option><option value="I">Independent</option></select></label>
    <label>Chamber: <select id="chamberFilter"><option value="all">All</option><option value="House">House</option><option value="Senate">Senate</option></select></label>
    <label>State: <select id="stateFilter"><option value="all">All</option></select></label>
    <label>Policy Area: <select id="networkPolicyFilter"><option value="all">All</option></select></label>
    <button id="resetBtn">Reset</button>
    <input type="text" id="search" placeholder="Search lawmaker...">
  </div>
  <div id="network"></div>
  <!-- Detail Panel -->
  <div id="detailPanel" class="detail-panel">
    <div class="detail-panel-header">
      <h3 id="detailName"></h3>
      <button id="detailClose">&times;</button>
    </div>
    <div id="detailMeta" class="detail-meta"></div>
    <div class="detail-stats" id="detailStats"></div>
    <div style="margin:10px 0 6px;">
      <label style="font-size:0.82rem;color:#8892b0;">Filter by policy area:
        <select id="detailPolicyFilter" class="policy-dropdown" style="width:100%;margin-top:4px;">
          <option value="all">All Policy Areas</option>
        </select>
      </label>
    </div>
    <div style="margin-bottom:4px;"><button class="export-btn" onclick="exportSvg('egoNetwork','Ego_Network')" style="float:right;">Export PNG</button><span style="font-size:0.85rem;color:#ccd6f6;">Ego Network</span></div>
    <div id="egoNetwork" style="width:100%;height:250px;background:#12141d;border-radius:6px;margin-bottom:10px;"></div>
    <h4 style="font-size:0.85rem;color:#ccd6f6;margin-bottom:6px;">Top Cooperating Colleagues</h4>
    <div id="detailColleagues" style="max-height:300px;overflow-y:auto;"></div>
  </div>
  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#5b9bf5"></div> Democrat</div>
    <div class="legend-item"><div class="legend-dot" style="background:#f56565"></div> Republican</div>
    <div class="legend-item"><div class="legend-dot" style="background:#b794f6"></div> Independent</div>
    <div class="legend-item"><div class="legend-line" style="border-color:#ffd93d;opacity:0.7"></div> Cross-party cooperation</div>
    <div class="legend-item"><div class="legend-line" style="border-color:#444;opacity:0.5"></div> Same-party cooperation</div>
  </div>
  <div class="tooltip" id="tooltip"></div>
  <div class="loading-overlay" id="networkLoading"><div class="loading-spinner">Rendering network...</div></div>
</div>

<!-- Top Tables -->
<div class="panel">
  <div class="tab-bar" id="detail-tab-bar">
    <button class="tab-btn active" data-tab="bp-tab">Bipartisan Score</button>
    <button class="tab-btn" data-tab="cent-tab">Highest Centrality</button>
  </div>
  <div class="tab-content active" id="bp-tab">
    <p class="subtitle">Fraction of a lawmaker's statistically significant cooperation ties that cross party lines. Scroll to see all lawmakers, from most to least bipartisan.</p>
    <div style="max-height:600px;overflow-y:auto;">
    <table id="bpTable"><thead><tr><th>#</th><th>Lawmaker</th><th>Party</th><th>State</th><th>Bipartisan Score</th><th></th></tr></thead><tbody></tbody></table>
    </div>
  </div>
  <div class="tab-content" id="cent-tab">
    <p class="subtitle">Betweenness centrality in the backbone network &mdash; high values indicate key bridges between communities. Scroll to see all lawmakers.</p>
    <div style="max-height:600px;overflow-y:auto;">
    <table id="centTable"><thead><tr><th>#</th><th>Lawmaker</th><th>Party</th><th>State</th><th>Betweenness</th><th></th></tr></thead><tbody></tbody></table>
    </div>
  </div>
</div>

<!-- Party & Policy Stats -->
<div class="panel">
  <h2>Bipartisanship by Party <button class="export-btn" onclick="exportPlotly('detailPartyChart','Bipartisanship_by_Party')">Export PNG</button></h2>
  <div id="detailPartyChart" class="chart-container"></div>
</div>

<div class="panel">
  <h2>Bipartisanship by Policy Area (ABI) <button class="export-btn" onclick="exportPlotly('detailPolicyChart','Bipartisanship_by_Policy_Area')">Export PNG</button></h2>
  <div id="detailPolicyChart" style="height:500px;"></div>
</div>

<!-- State chart -->
<div class="panel full-width">
  <h2>Bipartisanship by State <button class="export-btn" onclick="exportPlotly('detailStateChart','Bipartisanship_by_State')">Export PNG</button></h2>
  <div id="detailStateChart" style="height:400px;"></div>
</div>

<!-- Community Summary -->
<div class="panel full-width">
  <h2>Detected Communities (Clusters)</h2>
  <p style="font-size:0.82rem;color:#8892b0;margin-bottom:10px;">Clusters of lawmakers who frequently cosponsor together, detected via modularity optimization on the backbone network.</p>
  <table id="commTable"><thead><tr><th>Community</th><th>Size</th><th>Democrats</th><th>Republicans</th><th>Independents</th><th>Composition</th><th>Key Members</th></tr></thead><tbody></tbody></table>
</div>

</div><!-- end detail dashboard -->
</div><!-- end detail tab -->

<script>
// ═══════════════════════════════════════════════════════════════════════════════
// DATA
// ═══════════════════════════════════════════════════════════════════════════════
const tsData = {timeseries_json};
const policyMatrix = {policy_matrix_json};
const detailData = {detail_json};
const congressList = {congress_list_json};

const partyColor = {{ D: '#5b9bf5', R: '#f56565', I: '#b794f6', '?': '#888' }};
const partyName = {{ D: 'Democrat', R: 'Republican', I: 'Independent' }};
const plotBg = '#1a1d29';
const gridColor = '#2a2d3a';
const plotOpts = {{ responsive: true, displayModeBar: false }};

function shortName(fullName) {{
  return fullName.replace(/^(Rep\\.|Sen\\.)\\s*/, '').replace(/\\s*\\[.*\\]$/, '');
}}

function exportPlotly(chartId, filename) {{
  Plotly.downloadImage(chartId, {{ format: 'png', width: 1200, height: 700, filename: filename || chartId, scale: 2 }});
}}

function exportSvg(containerId, filename) {{
  const svg = document.querySelector('#' + containerId + ' svg');
  if (!svg) return;
  const clone = svg.cloneNode(true);
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  bg.setAttribute('width', '100%'); bg.setAttribute('height', '100%'); bg.setAttribute('fill', '#12141d');
  clone.insertBefore(bg, clone.firstChild);
  const serializer = new XMLSerializer();
  const svgStr = serializer.serializeToString(clone);
  const canvas = document.createElement('canvas');
  const scale = 2;
  canvas.width = (svg.clientWidth || 800) * scale;
  canvas.height = (svg.clientHeight || 650) * scale;
  const ctx = canvas.getContext('2d');
  const img = new Image();
  img.onload = function() {{
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const a = document.createElement('a');
    a.download = (filename || containerId) + '.png';
    a.href = canvas.toDataURL('image/png');
    a.click();
  }};
  img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgStr)));
}}

function ordinal(n) {{
  if (n % 100 >= 11 && n % 100 <= 13) return n + 'th';
  switch (n % 10) {{
    case 1: return n + 'st';
    case 2: return n + 'nd';
    case 3: return n + 'rd';
    default: return n + 'th';
  }}
}}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN TAB NAVIGATION
// ═══════════════════════════════════════════════════════════════════════════════
let detailInitialized = false;

document.querySelectorAll('.main-tab').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.main-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.main-tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    const tabEl = document.getElementById(btn.dataset.tab);
    tabEl.classList.add('active');
    if (btn.dataset.tab === 'detail-tab' && !detailInitialized) {{
      detailInitialized = true;
      initDetailTab();
    }}
    setTimeout(() => {{
      window.dispatchEvent(new Event('resize'));
    }}, 50);
  }});
}});

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 1: TRENDS OVER TIME
// ═══════════════════════════════════════════════════════════════════════════════

const shortLabels = tsData.map(d => d.congress + '');
const defaultLayout = {{
  paper_bgcolor: plotBg, plot_bgcolor: plotBg,
  font: {{ color: '#e0e0e0', size: 12 }},
  margin: {{ t: 30, b: 50, l: 60, r: 30 }},
  xaxis: {{ gridcolor: gridColor, tickvals: tsData.map(d => d.congress), ticktext: shortLabels }},
  yaxis: {{ gridcolor: gridColor, zeroline: false }},
  hovermode: 'x unified',
}};

// Summary Cards
const latest = tsData[tsData.length - 1];
const avgAbi = tsData.reduce((s, d) => s + d.bill_stats.adjusted_bipartisan_index, 0) / tsData.length;
const latestWeak = latest.polarization.weak;
const latestStrong = latest.polarization.strong;

document.getElementById('summary-cards').innerHTML = `
  <div class="metric-cards">
    <div class="metric-card"><div class="value">${{tsData.length}}</div><div class="label">Congresses Analyzed</div></div>
    <div class="metric-card"><div class="value">${{avgAbi.toFixed(3)}}</div><div class="label">Avg Adjusted Index</div></div>
    <div class="metric-card"><div class="value">${{latest.bill_stats.adjusted_bipartisan_index.toFixed(3)}}</div><div class="label">${{ordinal(latest.congress)}} ABI</div></div>
    <div class="metric-card"><div class="value">${{(latestWeak * 100).toFixed(1)}}%</div><div class="label">${{ordinal(latest.congress)}} Weak Polarization</div></div>
    <div class="metric-card"><div class="value">${{(latestStrong * 100).toFixed(1)}}%</div><div class="label">${{ordinal(latest.congress)}} Strong Polarization</div></div>
    <div class="metric-card"><div class="value">${{latest.network_stats.positive_edges.toLocaleString()}}</div><div class="label">${{ordinal(latest.congress)}} Positive Edges</div></div>
    <div class="metric-card"><div class="value">${{latest.network_stats.negative_edges.toLocaleString()}}</div><div class="label">${{ordinal(latest.congress)}} Negative Edges</div></div>
  </div>
`;

// Policy area dropdown
const policyAreaSelect = document.getElementById('policyAreaSelect');
Object.keys(policyMatrix).sort().forEach(area => {{
  const opt = document.createElement('option');
  opt.value = area;
  opt.textContent = area;
  policyAreaSelect.appendChild(opt);
}});

// ABI Chart
function renderAbiChart(selectedArea) {{
  const traces = [];
  const overallTrace = {{
    x: tsData.map(d => d.congress),
    y: tsData.map(d => d.bill_stats.adjusted_bipartisan_index),
    type: 'scatter', mode: 'lines+markers',
    name: 'Overall ABI',
    line: {{ color: selectedArea === 'overall' ? '#ffd93d' : 'rgba(255,217,61,0.3)', width: selectedArea === 'overall' ? 3 : 1.5 }},
    marker: {{ size: selectedArea === 'overall' ? 10 : 5 }},
    hovertemplate: '%{{x}}th Congress<br>Overall ABI: %{{y:.3f}}<extra></extra>',
  }};
  traces.push(overallTrace);

  if (selectedArea !== 'overall') {{
    const pm = policyMatrix[selectedArea];
    traces.push({{
      x: pm.congresses,
      y: pm.abi,
      type: 'scatter', mode: 'lines+markers',
      name: selectedArea,
      line: {{ color: '#ffd93d', width: 3 }},
      marker: {{ size: 10 }},
      connectgaps: false,
      hovertemplate: '%{{x}}th Congress<br>' + selectedArea + ' ABI: %{{y:.3f}}<extra></extra>',
    }});
  }}

  traces.push({{
    x: tsData.map(d => d.congress),
    y: tsData.map(() => 1.0),
    type: 'scatter', mode: 'lines',
    name: 'Random baseline (1.0)',
    line: {{ color: '#555', dash: 'dash', width: 1 }},
    hoverinfo: 'skip',
  }});

  const maxAbi = selectedArea !== 'overall'
    ? Math.max(1.2, ...tsData.map(d => d.bill_stats.adjusted_bipartisan_index * 1.1),
        ...(policyMatrix[selectedArea]?.abi.filter(v => v !== null).map(v => v * 1.1) || []))
    : Math.max(1.2, ...tsData.map(d => d.bill_stats.adjusted_bipartisan_index * 1.1));

  Plotly.react('abiChart', traces, {{
    ...defaultLayout,
    yaxis: {{ ...defaultLayout.yaxis, title: 'Adjusted Bipartisan Index', range: [0, maxAbi] }},
    legend: {{ x: 0.02, y: 0.98, bgcolor: 'rgba(0,0,0,0.3)' }},
  }}, plotOpts);
}}

renderAbiChart('overall');
policyAreaSelect.addEventListener('change', function() {{ renderAbiChart(this.value); }});

// ── Polarization Trends Chart ──
Plotly.newPlot('polarizationChart', [
  {{
    x: tsData.map(d => d.congress),
    y: tsData.map(d => d.polarization.weak * 100),
    name: 'Weak Polarization (party sorting)',
    type: 'scatter', mode: 'lines+markers',
    line: {{ color: '#ffa726', width: 3 }},
    marker: {{ size: 8 }},
    hovertemplate: '%{{x}}th Congress<br>Weak: %{{y:.1f}}%<extra></extra>',
  }},
  {{
    x: tsData.map(d => d.congress),
    y: tsData.map(d => d.polarization.strong * 100),
    name: 'Strong Polarization (active avoidance)',
    type: 'scatter', mode: 'lines+markers',
    line: {{ color: '#ef5350', width: 3 }},
    marker: {{ size: 8 }},
    hovertemplate: '%{{x}}th Congress<br>Strong: %{{y:.1f}}%<extra></extra>',
  }},
], {{
  ...defaultLayout,
  yaxis: {{ ...defaultLayout.yaxis, title: 'Polarization (%)' }},
  legend: {{ x: 0.02, y: 0.98, bgcolor: 'rgba(0,0,0,0.3)' }},
  annotations: [{{
    x: 0.5, y: 1.06, xref: 'paper', yref: 'paper',
    text: 'Weak = same-party edge fraction | Strong = negative-edge fraction among backbone',
    showarrow: false, font: {{ size: 11, color: '#8892b0' }},
  }}],
}}, plotOpts);

// Raw vs Expected
Plotly.newPlot('rawExpChart', [
  {{
    x: tsData.map(d => d.congress),
    y: tsData.map(d => d.bill_stats.raw_bipartisan_rate * 100),
    name: 'Raw Bipartisan Rate',
    type: 'scatter', mode: 'lines+markers',
    line: {{ color: '#5b9bf5', width: 2 }},
    hovertemplate: '%{{y:.1f}}%<extra>Raw</extra>',
  }},
  {{
    x: tsData.map(d => d.congress),
    y: tsData.map(d => d.bill_stats.expected_cross_party_rate * 100),
    name: 'Expected (random mixing)',
    type: 'scatter', mode: 'lines+markers',
    line: {{ color: '#f56565', width: 2, dash: 'dot' }},
    hovertemplate: '%{{y:.1f}}%<extra>Expected</extra>',
  }},
], {{
  ...defaultLayout,
  yaxis: {{ ...defaultLayout.yaxis, title: 'Rate (%)' }},
  legend: {{ x: 0.02, y: 0.98, bgcolor: 'rgba(0,0,0,0.3)' }},
}}, plotOpts);

// Party Composition
Plotly.newPlot('partyCompChart', [
  {{ x: tsData.map(d => d.congress), y: tsData.map(d => d.party_composition.D), name: 'Democrats', type: 'bar', marker: {{ color: '#5b9bf5' }} }},
  {{ x: tsData.map(d => d.congress), y: tsData.map(d => d.party_composition.R), name: 'Republicans', type: 'bar', marker: {{ color: '#f56565' }} }},
  {{ x: tsData.map(d => d.congress), y: tsData.map(d => d.party_composition.Other), name: 'Other', type: 'bar', marker: {{ color: '#b794f6' }} }},
], {{
  ...defaultLayout,
  barmode: 'stack',
  yaxis: {{ ...defaultLayout.yaxis, title: 'Active Legislators' }},
  legend: {{ x: 0.02, y: 0.98, bgcolor: 'rgba(0,0,0,0.3)' }},
}}, plotOpts);

// Policy Area Heatmap
(function() {{
  const areas = Object.keys(policyMatrix).sort();
  const congresses = tsData.map(d => d.congress);
  const zValues = [];
  const hoverText = [];

  areas.forEach(area => {{
    const row = [];
    const hRow = [];
    policyMatrix[area].abi.forEach((val, i) => {{
      row.push(val);
      const count = policyMatrix[area].bill_count[i];
      hRow.push(val !== null
        ? `${{area}}<br>${{ordinal(congresses[i])}} Congress<br>ABI: ${{val.toFixed(3)}}<br>Bills: ${{count}}`
        : `${{area}}<br>${{ordinal(congresses[i])}} Congress<br>Insufficient data`);
    }});
    zValues.push(row);
    hoverText.push(hRow);
  }});

  Plotly.newPlot('policyHeatmap', [{{
    z: zValues,
    x: congresses.map(c => ordinal(c)),
    y: areas,
    type: 'heatmap',
    colorscale: [
      [0, '#1a1d29'],
      [0.3, '#2a4a7f'],
      [0.5, '#4a6fa5'],
      [0.7, '#ffd93d'],
      [1.0, '#ff6b6b'],
    ],
    zmin: 0,
    zmax: Math.max(1.5, ...zValues.flat().filter(v => v !== null)) * 0.9,
    hoverongaps: false,
    text: hoverText,
    hoverinfo: 'text',
    colorbar: {{
      title: {{ text: 'ABI', side: 'right' }},
      tickfont: {{ color: '#e0e0e0' }},
      titlefont: {{ color: '#e0e0e0' }},
    }},
  }}], {{
    paper_bgcolor: plotBg, plot_bgcolor: plotBg,
    font: {{ color: '#e0e0e0', size: 11 }},
    margin: {{ t: 10, b: 60, l: 220, r: 80 }},
    xaxis: {{ side: 'bottom', tickangle: -45 }},
    yaxis: {{ autorange: 'reversed', dtick: 1, tickfont: {{ size: 10 }} }},
  }}, plotOpts);
}})();

// Top Lawmakers Table
(function() {{
  const topDiv = document.getElementById('topLawmakers');
  let html = '<table><thead><tr><th>Congress</th><th>Years</th>';
  for (let i = 1; i <= 5; i++) html += `<th>#${{i}}</th>`;
  html += '</tr></thead><tbody>';

  tsData.forEach(d => {{
    const top5 = d.top_bipartisan_lawmakers.slice(0, 5);
    html += `<tr><td>${{ordinal(d.congress)}}</td><td>${{d.years}}</td>`;
    top5.forEach(lm => {{
      const name = shortName(lm.name);
      html += `<td><span class="party-${{lm.party}}">${{name}}</span><br><span style="font-size:0.72rem;color:#8892b0">${{(lm.bipartisan_score*100).toFixed(0)}}% (${{lm.bipartisan_bills}}/${{lm.total_bills}})</span></td>`;
    }});
    for (let i = top5.length; i < 5; i++) html += '<td>-</td>';
    html += '</tr>';
  }});
  html += '</tbody></table>';
  topDiv.innerHTML = html;
}})();

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 2: PER-CONGRESS DETAIL
// ═══════════════════════════════════════════════════════════════════════════════

let currentCongressNum = null;
let currentSimulation = null;

function initDetailTab() {{
  const sel = document.getElementById('congressSelect');
  congressList.forEach(c => {{
    const opt = document.createElement('option');
    opt.value = c.num;
    opt.textContent = c.label;
    sel.appendChild(opt);
  }});
  sel.value = congressList[congressList.length - 1].num;
  sel.addEventListener('change', function() {{ loadCongress(+this.value); }});
  loadCongress(+sel.value);
}}

function loadCongress(congressNum) {{
  currentCongressNum = congressNum;
  const detail = detailData[congressNum];
  if (!detail) return;

  const info = congressList.find(c => c.num === congressNum);
  const summary = detail.summary || {{}};
  const posEdges = detail.edges.filter(e => e.sign === 'positive').length;
  const negEdges = detail.edges.filter(e => e.sign === 'negative').length;
  document.getElementById('congressInfo').textContent =
    `${{info.node_count}} lawmakers | ${{posEdges}} positive + ${{negEdges}} negative backbone edges`;

  // Reset filters — default to positive-only to avoid edge overload
  document.getElementById('edgeTypeFilter').value = 'positive';
  document.getElementById('partyFilter').value = 'all';
  document.getElementById('chamberFilter').value = 'all';
  document.getElementById('stateFilter').value = 'all';
  document.getElementById('search').value = '';

  // Populate state filter
  const sf = document.getElementById('stateFilter');
  sf.innerHTML = '<option value="all">All</option>';
  const states = [...new Set(detail.nodes.map(n => n.state))].sort();
  states.forEach(s => {{ const o = document.createElement('option'); o.value = s; o.textContent = s; sf.appendChild(o); }});

  // Populate network policy area filter
  const pf = document.getElementById('networkPolicyFilter');
  pf.innerHTML = '<option value="all">All</option>';
  const areas = detail.policy_areas_list || [];
  areas.forEach(a => {{ const o = document.createElement('option'); o.value = a; o.textContent = a; pf.appendChild(o); }});

  // Close detail panel
  document.getElementById('detailPanel').classList.remove('open');

  // Tables
  fillTable('bpTable', detail.top_bipartisan, 'bipartisan_score', v => (v * 100).toFixed(1) + '%');
  fillTable('centTable', detail.top_betweenness, 'betweenness', v => v.toFixed(5));

  // Charts
  renderPartyChart(detail.party_summary);
  renderPolicyChart(detail.policy_summary);
  renderStateChart(detail.state_summary);
  renderCommTable(detail.comm_summary, detail.comm_top_members, detail.nodes);

  setTimeout(() => renderNetwork(detail), 100);
}}

function fillTable(tableId, data, valueKey, valueFmt) {{
  const tbody = document.querySelector(`#${{tableId}} tbody`);
  const maxVal = data.length > 0 ? data[0][valueKey] : 1;
  tbody.innerHTML = data.map((d, i) => `
    <tr>
      <td>${{i+1}}</td>
      <td>${{shortName(d.name)}}</td>
      <td class="party-${{d.party}}">${{d.party}}</td>
      <td>${{d.state}}</td>
      <td>${{valueFmt(d[valueKey])}}</td>
      <td><div class="bar-fill" style="width:${{Math.round(d[valueKey] / maxVal * 100)}}%;background:${{partyColor[d.party]}}"></div></td>
    </tr>
  `).join('');
}}

function renderPartyChart(partySummary) {{
  const keys = Object.keys(partySummary).filter(k => k !== '?').sort();
  Plotly.react('detailPartyChart', [{{
    x: keys.map(k => partyName[k] || k),
    y: keys.map(k => (partySummary[k] * 100)),
    type: 'bar',
    marker: {{ color: keys.map(k => partyColor[k]) }},
    text: keys.map(k => (partySummary[k] * 100).toFixed(1) + '%'),
    textposition: 'outside',
    hovertemplate: '%{{x}}: %{{y:.1f}}%<extra></extra>'
  }}], {{
    paper_bgcolor: plotBg, plot_bgcolor: plotBg,
    font: {{ color: '#e0e0e0', size: 12 }},
    yaxis: {{ title: 'Avg Bipartisan Score (%)', gridcolor: gridColor, zeroline: false }},
    xaxis: {{ tickfont: {{ size: 14 }} }},
    margin: {{ t: 30, b: 50, l: 60, r: 20 }},
    bargap: 0.4,
  }}, plotOpts);
}}

function renderPolicyChart(policySummary) {{
  const keys = Object.keys(policySummary).sort((a,b) => policySummary[b].pct - policySummary[a].pct);
  Plotly.react('detailPolicyChart', [{{
    y: keys,
    x: keys.map(k => policySummary[k].pct),
    type: 'bar', orientation: 'h',
    marker: {{ color: keys.map(k => {{ const v = policySummary[k].pct / 100; return `hsl(${{120 * Math.min(v, 1)}}, 70%, 45%)`; }}) }},
    text: keys.map(k => `ABI: ${{(policySummary[k].pct / 100).toFixed(3)}} (${{policySummary[k].total}} bills)`),
    textposition: 'outside', textfont: {{ size: 10 }},
    hovertemplate: '%{{y}}<br>ABI: %{{x:.1f}}%<extra></extra>',
  }}], {{
    paper_bgcolor: plotBg, plot_bgcolor: plotBg,
    font: {{ color: '#e0e0e0', size: 11 }},
    xaxis: {{ title: 'ABI x 100', gridcolor: gridColor, zeroline: false }},
    yaxis: {{ autorange: 'reversed', tickfont: {{ size: 10 }}, dtick: 1 }},
    margin: {{ t: 10, b: 40, l: 220, r: 120 }},
    bargap: 0.25,
  }}, plotOpts);
}}

function renderStateChart(stateSummary) {{
  const keys = Object.keys(stateSummary).sort((a,b) => stateSummary[b].avg_bp - stateSummary[a].avg_bp);
  Plotly.react('detailStateChart', [{{
    x: keys,
    y: keys.map(s => (stateSummary[s].avg_bp * 100)),
    type: 'bar',
    marker: {{ color: keys.map(s => {{ const v = stateSummary[s].avg_bp; return `hsl(${{120 * v}}, 70%, 50%)`; }}) }},
    text: keys.map(s => `${{(stateSummary[s].avg_bp * 100).toFixed(1)}}% (${{stateSummary[s].count}})`),
    textposition: 'outside', textfont: {{ size: 9 }},
    hovertemplate: '%{{x}}: %{{y:.1f}}% (n=%{{customdata}})<extra></extra>',
    customdata: keys.map(s => stateSummary[s].count),
  }}], {{
    paper_bgcolor: plotBg, plot_bgcolor: plotBg,
    font: {{ color: '#e0e0e0', size: 11 }},
    yaxis: {{ title: 'Avg Bipartisan Score (%)', gridcolor: gridColor, zeroline: false }},
    xaxis: {{ tickangle: -45, tickfont: {{ size: 10 }} }},
    margin: {{ t: 20, b: 80, l: 60, r: 20 }},
    bargap: 0.3,
  }}, plotOpts);
}}

function renderCommTable(commSummary, commTopMembers, nodes) {{
  const tbody = document.querySelector('#commTable tbody');
  tbody.innerHTML = '';
  Object.entries(commSummary).forEach(([cid, c]) => {{
    const topMembers = commTopMembers[cid] || [];
    const dPct = Math.round(c.D / c.size * 100);
    const rPct = Math.round(c.R / c.size * 100);
    const iPct = Math.round((c.I || 0) / c.size * 100);
    const compBar = `<div style="display:flex;height:14px;border-radius:3px;overflow:hidden;width:100%">` +
      (dPct > 0 ? `<div style="width:${{dPct}}%;background:#5b9bf5" title="${{dPct}}% D"></div>` : '') +
      (rPct > 0 ? `<div style="width:${{rPct}}%;background:#f56565" title="${{rPct}}% R"></div>` : '') +
      (iPct > 0 ? `<div style="width:${{iPct}}%;background:#b794f6" title="${{iPct}}% I"></div>` : '') +
      `</div>`;
    tbody.innerHTML += `<tr>
      <td>${{parseInt(cid)+1}}</td>
      <td>${{c.size}}</td>
      <td class="party-D">${{c.D}}</td>
      <td class="party-R">${{c.R}}</td>
      <td class="party-I">${{c.I || 0}}</td>
      <td style="min-width:100px">${{compBar}}</td>
      <td style="font-size:0.78rem">${{topMembers.map(m => `<span class="party-${{m.party}}">${{shortName(m.name)}}</span>`).join(', ')}}</td>
    </tr>`;
  }});
}}

// Inner tab buttons for detail tab
document.getElementById('detail-tab-bar').addEventListener('click', function(e) {{
  const btn = e.target.closest('.tab-btn');
  if (!btn) return;
  const panel = btn.closest('.panel');
  panel.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  panel.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.tab).classList.add('active');
}});

// ═══════════════════════════════════════════════════════════════════════════════
// D3 FORCE NETWORK (with signed edge rendering)
// ═══════════════════════════════════════════════════════════════════════════════

function renderNetwork(detail) {{
  const container = document.getElementById('network');
  container.innerHTML = '';
  const tooltip = document.getElementById('tooltip');
  const width = container.clientWidth || 800;
  const height = container.clientHeight || 650;

  if (currentSimulation) {{
    currentSimulation.stop();
    currentSimulation = null;
  }}

  const nodesRaw = detail.nodes;
  const edgesRaw = detail.edges;

  const svg = d3.select('#network').append('svg')
    .attr('width', width).attr('height', height)
    .attr('viewBox', [0, 0, width, height]);

  const g = svg.append('g');
  svg.call(d3.zoom().scaleExtent([0.1, 8]).on('zoom', e => g.attr('transform', e.transform)));

  let edgeTypeFilterVal = 'positive';
  let partyFilterVal = 'all';
  let chamberFilterVal = 'all';
  let stateFilterVal = 'all';
  let policyAreaFilterVal = 'all';
  let highlightedNode = null;
  let searchTerm = '';

  const maxDeg = d3.max(nodesRaw, d => d.degree_centrality) || 1;
  function nodeRadius(d) {{ return 3 + 15 * Math.sqrt(d.degree_centrality / maxDeg); }}

  function getFilteredData() {{
    let nodes = nodesRaw.filter(n => {{
      if (partyFilterVal !== 'all' && n.party !== partyFilterVal) return false;
      if (chamberFilterVal !== 'all' && n.chamber !== chamberFilterVal) return false;
      if (stateFilterVal !== 'all' && n.state !== stateFilterVal) return false;
      return true;
    }});
    const nodeIds = new Set(nodes.map(n => n.id));
    let edges = edgesRaw.filter(e => {{
      const src = e.source.id || e.source;
      const tgt = e.target.id || e.target;
      if (!nodeIds.has(src) || !nodeIds.has(tgt)) return false;
      // Only show positive (cooperation) edges; optionally filter to cross-party
      if (e.sign !== 'positive') return false;
      if (edgeTypeFilterVal === 'cross_party' && !e.cross_party) return false;
      // Policy area filter
      if (policyAreaFilterVal !== 'all') {{
        const paWeight = (e.policy_areas && e.policy_areas[policyAreaFilterVal]) || 0;
        if (paWeight === 0) return false;
      }}
      return true;
    }});
    const connectedIds = new Set();
    edges.forEach(e => {{ connectedIds.add(e.source.id || e.source); connectedIds.add(e.target.id || e.target); }});
    nodes = nodes.filter(n => connectedIds.has(n.id));
    return {{
      nodes: nodes.map(n => ({{...n}})),
      edges: edges.map(e => {{
        let w = e.weight;
        if (policyAreaFilterVal !== 'all') w = (e.policy_areas && e.policy_areas[policyAreaFilterVal]) || 0;
        return {{source: e.source.id || e.source, target: e.target.id || e.target, weight: w, cross_party: e.cross_party, sign: e.sign, policy_areas: e.policy_areas}};
      }})
    }};
  }}

  let linkSel, nodeSel;

  function render() {{
    g.selectAll('*').remove();
    if (currentSimulation) {{ currentSimulation.stop(); currentSimulation = null; }}
    const data = getFilteredData();

    if (data.nodes.length === 0) {{
      g.append('text').attr('x', width/2).attr('y', height/2).attr('text-anchor', 'middle')
        .attr('fill', '#8892b0').text('No nodes match current filters. Try adjusting.');
      return;
    }}

    const maxWeight = d3.max(data.edges, e => e.weight) || 1;

    linkSel = g.append('g').selectAll('line').data(data.edges).join('line')
      .attr('stroke', d => d.cross_party ? '#ffd93d' : '#444')
      .attr('stroke-opacity', d => d.cross_party ? 0.35 : 0.15)
      .attr('stroke-width', d => 0.5 + 2 * (d.weight / maxWeight));

    nodeSel = g.append('g').selectAll('circle').data(data.nodes).join('circle')
      .attr('r', d => nodeRadius(d))
      .attr('fill', d => {{
        if (searchTerm && shortName(d.name).toLowerCase().includes(searchTerm)) return '#ffd93d';
        return partyColor[d.party] || '#888';
      }})
      .attr('stroke', d => d.id === highlightedNode ? '#fff' : '#000')
      .attr('stroke-width', d => d.id === highlightedNode ? 2.5 : 0.5)
      .attr('opacity', d => {{
        if (highlightedNode) {{
          if (d.id === highlightedNode) return 1;
          const hn = highlightedNode;
          const connected = data.edges.some(e =>
            (e.source.id === hn && e.target.id === d.id) ||
            (e.target.id === hn && e.source.id === d.id) ||
            (e.source === hn && e.target === d.id) ||
            (e.target === hn && e.source === d.id)
          );
          return connected ? 0.9 : 0.15;
        }}
        return 0.85;
      }})
      .call(d3.drag()
        .on('start', (e, d) => {{ if (!e.active) currentSimulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
        .on('drag', (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
        .on('end', (e, d) => {{ if (!e.active) currentSimulation.alphaTarget(0); d.fx = null; d.fy = null; }})
      );

    nodeSel.on('mouseover', (e, d) => {{
      tooltip.style.opacity = 1;
      tooltip.innerHTML = `
        <div class="name party-${{d.party}}">${{shortName(d.name)}}</div>
        <div class="stat">Party: ${{partyName[d.party] || d.party}} | State: ${{d.state}} | ${{d.chamber}}</div>
        <div class="stat">Bipartisan Score: <b>${{(d.bipartisan_score*100).toFixed(1)}}%</b></div>
        <div class="stat">Positive ties: ${{d.positive_ties || 0}} | Negative ties: ${{d.negative_ties || 0}}</div>
        <div class="stat">Cross-party positive: ${{d.cross_party_positive || 0}}</div>
        <div class="stat">Betweenness: ${{d.betweenness.toFixed(5)}} | Eigenvector: ${{d.eigenvector.toFixed(4)}}</div>
        <div class="stat">Community: ${{d.community+1}} | Bills: ${{d.total_bills || 0}}</div>
      `;
    }}).on('mousemove', (e) => {{
      const rect = document.getElementById('network-container').getBoundingClientRect();
      tooltip.style.left = (e.clientX - rect.left + 15) + 'px';
      tooltip.style.top = (e.clientY - rect.top - 10) + 'px';
    }}).on('mouseout', () => {{
      tooltip.style.opacity = 0;
    }}).on('click', (e, d) => {{
      if (highlightedNode === d.id) {{
        highlightedNode = null;
        closeDetailPanel();
      }} else {{
        highlightedNode = d.id;
        showDetailPanel(d, edgesRaw, nodesRaw);
      }}
      render();
    }});

    currentSimulation = d3.forceSimulation(data.nodes)
      .force('link', d3.forceLink(data.edges).id(d => d.id).distance(60).strength(d => {{
        // Positive edges attract, negative edges have no attraction
        return 0.1 + 0.3 * (d.weight / maxWeight);
      }}))
      .force('charge', d3.forceManyBody().strength(-80))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(d => nodeRadius(d) + 2))
      .force('x', d3.forceX(width / 2).strength(0.05))
      .force('y', d3.forceY(height / 2).strength(0.05))
      .on('tick', () => {{
        linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
               .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        nodeSel.attr('cx', d => d.x).attr('cy', d => d.y);
      }});
  }}

  render();

  // Controls
  document.getElementById('edgeTypeFilter').addEventListener('change', function() {{
    edgeTypeFilterVal = this.value;
    render();
  }});
  document.getElementById('partyFilter').addEventListener('change', function() {{ partyFilterVal = this.value; render(); }});
  document.getElementById('chamberFilter').addEventListener('change', function() {{ chamberFilterVal = this.value; render(); }});
  document.getElementById('stateFilter').addEventListener('change', function() {{ stateFilterVal = this.value; render(); }});
  document.getElementById('networkPolicyFilter').addEventListener('change', function() {{
    policyAreaFilterVal = this.value;
    render();
  }});
  document.getElementById('resetBtn').addEventListener('click', function() {{
    edgeTypeFilterVal = 'positive';
    partyFilterVal = 'all';
    chamberFilterVal = 'all';
    stateFilterVal = 'all';
    policyAreaFilterVal = 'all';
    highlightedNode = null;
    searchTerm = '';
    document.getElementById('edgeTypeFilter').value = 'positive';
    document.getElementById('partyFilter').value = 'all';
    document.getElementById('chamberFilter').value = 'all';
    document.getElementById('stateFilter').value = 'all';
    document.getElementById('networkPolicyFilter').value = 'all';
    document.getElementById('search').value = '';
    closeDetailPanel();
    render();
  }});
  document.getElementById('search').addEventListener('input', function() {{
    searchTerm = this.value.toLowerCase().trim();
    render();
  }});
  document.getElementById('search').addEventListener('keydown', function(evt) {{
    if (evt.key === 'Enter' && searchTerm) {{
      const match = nodesRaw.find(n => shortName(n.name).toLowerCase().includes(searchTerm));
      if (match) {{
        highlightedNode = match.id;
        showDetailPanel(match, edgesRaw, nodesRaw);
        render();
      }}
    }}
  }});

  // Detail panel functions
  function showDetailPanel(node, edges, nodes) {{
    const panel = document.getElementById('detailPanel');
    panel.classList.add('open');
    document.getElementById('detailName').innerHTML = `<span class="party-${{node.party}}">${{shortName(node.name)}}</span>`;
    document.getElementById('detailMeta').innerHTML = `${{partyName[node.party] || node.party}} &middot; ${{node.state}} &middot; ${{node.chamber}}`;
    document.getElementById('detailStats').innerHTML = `
      <div class="detail-stat"><div class="val">${{(node.bipartisan_score*100).toFixed(1)}}%</div><div class="lbl">Bipartisan Score</div></div>
      <div class="detail-stat"><div class="val">${{node.bills_sponsored || 0}}</div><div class="lbl">Bills Sponsored</div></div>
      <div class="detail-stat"><div class="val"><span style="color:#4caf50">${{node.positive_ties || 0}}</span>/<span style="color:#ef5350">${{node.negative_ties || 0}}</span></div><div class="lbl">Pos / Neg Ties</div></div>
      <div class="detail-stat"><div class="val">${{node.cross_party_positive || 0}}</div><div class="lbl">Cross-Party Positive</div></div>
    `;

    // Populate detail policy area filter
    const dpf = document.getElementById('detailPolicyFilter');
    dpf.innerHTML = '<option value="all">All Policy Areas</option>';
    const nodeAreas = node.policy_areas || {{}};
    Object.keys(nodeAreas).sort().forEach(a => {{
      const o = document.createElement('option');
      o.value = a;
      o.textContent = `${{a}} (${{nodeAreas[a]}})`;
      dpf.appendChild(o);
    }});
    dpf.onchange = function() {{ renderEgoData(node, edges, nodes, this.value); }};

    renderEgoData(node, edges, nodes, 'all');
  }}

  function renderEgoData(node, edges, nodes, policyFilter) {{
    // Find connected positive edges (cooperation)
    let egoEdges = edges.filter(e => {{
      const src = e.source.id || e.source;
      const tgt = e.target.id || e.target;
      return (src === node.id || tgt === node.id) && e.sign === 'positive';
    }});

    let neighbors = {{}};
    egoEdges.forEach(e => {{
      const src = e.source.id || e.source;
      const tgt = e.target.id || e.target;
      const nid = src === node.id ? tgt : src;
      let w = e.weight;
      if (policyFilter !== 'all') {{
        w = (e.policy_areas && e.policy_areas[policyFilter]) || 0;
      }}
      if (w > 0) {{
        neighbors[nid] = (neighbors[nid] || 0) + w;
      }}
    }});

    const sorted = Object.entries(neighbors)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 20);
    const maxW = sorted.length > 0 ? sorted[0][1] : 1;

    const nodeMap = {{}};
    nodes.forEach(n => {{ nodeMap[n.id] = n; }});

    const colDiv = document.getElementById('detailColleagues');
    if (sorted.length === 0) {{
      colDiv.innerHTML = '<div style="color:#555;font-size:0.8rem;padding:8px;">No positive connections for this filter.</div>';
    }} else {{
      colDiv.innerHTML = sorted.map(([nid, w], i) => {{
        const n = nodeMap[nid] || {{ name: nid, party: '?' }};
        return `<div class="colleague-row">
          <span class="rank">${{i+1}}</span>
          <span class="cname"><span class="party-${{n.party}}">${{shortName(n.name)}}</span><br><span style="font-size:0.7rem;color:#555">${{n.party}} - ${{n.state || '?'}}</span></span>
          <span class="ccount">${{w}}</span>
        </div>
        <div class="colleague-bar" style="width:${{Math.round(w/maxW*100)}}%"></div>`;
      }}).join('');
    }}

    renderEgoNetwork(node, sorted, nodeMap);
  }}

  function renderEgoNetwork(centerNode, sortedNeighbors, nodeMap) {{
    const container = document.getElementById('egoNetwork');
    container.innerHTML = '';
    const w = container.clientWidth || 330;
    const h = container.clientHeight || 250;

    const egoNodes = [{{ ...centerNode, fx: w/2, fy: h/2, isCenter: true }}];
    const egoEdges = [];
    sortedNeighbors.forEach(([nid, weight]) => {{
      const n = nodeMap[nid];
      if (n) {{
        egoNodes.push({{ ...n, isCenter: false }});
        egoEdges.push({{ source: centerNode.id, target: nid, weight }});
      }}
    }});

    if (egoNodes.length <= 1) {{
      container.innerHTML = '<div style="color:#555;font-size:0.8rem;text-align:center;padding-top:40%;">No connections</div>';
      return;
    }}

    const maxW = d3.max(egoEdges, e => e.weight) || 1;
    const svg = d3.select(container).append('svg').attr('width', w).attr('height', h);
    const g = svg.append('g');

    const linkSel = g.append('g').selectAll('line').data(egoEdges).join('line')
      .attr('stroke', d => {{
        const src = nodeMap[d.source.id || d.source] || centerNode;
        const tgt = nodeMap[d.target.id || d.target] || {{}};
        return (src.party !== tgt.party && src.party !== '?' && tgt.party !== '?') ? '#ffd93d' : '#444';
      }})
      .attr('stroke-opacity', 0.5)
      .attr('stroke-width', d => 1 + 3 * (d.weight / maxW));

    const nodeSel = g.append('g').selectAll('circle').data(egoNodes).join('circle')
      .attr('r', d => d.isCenter ? 12 : 6 + 4 * Math.sqrt((sortedNeighbors.find(s => s[0] === d.id)?.[1] || 0) / maxW))
      .attr('fill', d => partyColor[d.party] || '#888')
      .attr('stroke', d => d.isCenter ? '#fff' : '#000')
      .attr('stroke-width', d => d.isCenter ? 2 : 0.5)
      .attr('opacity', 0.9);

    const labelNodes = egoNodes.filter((n, i) => i === 0 || i <= 5);
    const labelSel = g.append('g').selectAll('text').data(labelNodes).join('text')
      .text(d => shortName(d.name).split(' ').pop())
      .attr('fill', '#ccd6f6')
      .attr('font-size', d => d.isCenter ? '9px' : '7px')
      .attr('text-anchor', 'middle')
      .attr('dy', d => d.isCenter ? -16 : -10);

    const sim = d3.forceSimulation(egoNodes)
      .force('link', d3.forceLink(egoEdges).id(d => d.id).distance(50).strength(0.4))
      .force('charge', d3.forceManyBody().strength(-60))
      .force('center', d3.forceCenter(w/2, h/2))
      .force('collision', d3.forceCollide(10))
      .on('tick', () => {{
        linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
               .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
        nodeSel.attr('cx', d => d.x).attr('cy', d => d.y);
        labelSel.attr('x', d => d.x).attr('y', d => d.y);
      }});
  }}

  function closeDetailPanel() {{
    document.getElementById('detailPanel').classList.remove('open');
  }}

  document.getElementById('detailClose').addEventListener('click', () => {{
    highlightedNode = null;
    closeDetailPanel();
    render();
  }});
}}

</script>
</body>
</html>"""


def main():
    start_time = time.time()
    all_metrics = []

    print(f"Loading pre-computed metrics for congresses {CONGRESSES[0]}-{CONGRESSES[-1]}...\n")

    for congress in CONGRESSES:
        t0 = time.time()
        print(f"  {ordinal(congress)} Congress ({congress_to_years(congress)})...", end=" ", flush=True)
        data = load_metrics(congress)
        if data:
            all_metrics.append(data)
            s = data["summary"]
            pc = data["party_composition"]
            elapsed = time.time() - t0
            print(
                f"D:{pc.get('D',0)} R:{pc.get('R',0)} | "
                f"ABI:{s['abi']:.3f} | "
                f"Weak:{s['weak_polarization']:.3f} Strong:{s['strong_polarization']:.3f} | "
                f"Pos:{s['positive_edges']} Neg:{s['negative_edges']} | "
                f"Nodes:{len(data['nodes'])} Edges:{len(data['edges'])} | "
                f"{elapsed:.2f}s"
            )
        else:
            print("SKIPPED")

    if not all_metrics:
        print("ERROR: No metrics files found. Run Stages 1-4 first.")
        sys.exit(1)

    print(f"\nAssembling dashboard data...")
    timeseries_data, policy_matrix, detail_data, congress_list = assemble_data(all_metrics)

    print(f"Generating HTML dashboard...")
    html = generate_html(timeseries_data, policy_matrix, detail_data, congress_list)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    file_size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    total_time = time.time() - start_time

    print(f"\nDone! Output: {OUTPUT_FILE} ({file_size_mb:.1f} MB)")
    print(f"Total time: {total_time:.1f}s")

    # Summary table
    print(f"\n  {'Congress':<10} {'Years':<12} {'D':>4} {'R':>4} {'ABI':>7} {'Weak%':>7} {'Strong%':>8} {'Pos':>6} {'Neg':>6}")
    print(f"  {'-'*72}")
    for m in all_metrics:
        s = m["summary"]
        pc = m["party_composition"]
        print(
            f"  {ordinal(m['congress']):<10} {m['years']:<12} "
            f"{pc.get('D',0):>4} {pc.get('R',0):>4} "
            f"{s['abi']:>7.3f} "
            f"{s['weak_polarization']*100:>6.1f}% "
            f"{s['strong_polarization']*100:>7.1f}% "
            f"{s['positive_edges']:>6} "
            f"{s['negative_edges']:>6}"
        )


if __name__ == "__main__":
    main()
