# Quantifying Congressional Bipartisanship

An interactive dashboard that quantifies bipartisan cooperation in U.S. Congressional bill cosponsorship (108th-119th Congress, 2003-2027), using the Stochastic Degree Sequence Model (SDSM) to extract statistically significant collaboration ties from raw cosponsorship data.

## Features

- **SDSM Backbone Network** — Statistically significant cooperation ties (not raw co-occurrence), visualized as an interactive D3 force-directed graph
- **Signed Network Analysis** — Positive edges (significant cooperation) and negative edges (significant avoidance) enable weak vs. strong polarization measurement
- **Adjusted Bipartisan Index (ABI)** — Bipartisan rate normalized by party composition, tracked over time
- **Polarization Trends** — Weak polarization (absent ties) vs. strong polarization (avoidance ties) across congresses
- **Policy-Area Decomposition** — Per-domain backbone extraction reveals whether bipartisanship is domain-specific
- **Lawmaker Detail Panel** — Click any node to see ego network, top cooperating colleagues, and policy breakdown
- **Leaderboards** — All lawmakers ranked by bipartisan score and betweenness centrality on the backbone
- **Policy Area Heatmap** — ABI by policy area over time
- **Community Detection** — Clusters of lawmakers detected via modularity optimization

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Stage 1: Download bill data from GovInfo (108th-119th Congress)
# No API key needed. Takes ~10-30 minutes.
python download_bills.py

# Stage 2: Project bipartite bill-legislator network into legislator-legislator edges
python build_network.py

# Stage 3: Extract signed backbone via SDSM (statistical significance testing)
python build_backbone.py

# Stage 4: Compute centrality, ABI, polarization, and community metrics
python compute_metrics.py

# Stage 5: Generate self-contained HTML dashboard
python build_dashboard.py
```

Open `bipartisanship_dashboard.html` in any modern browser. No server required.

## Pipeline Architecture

```
Stage 1: download_bills.py    → bills_by_congress/bills_{108..119}.json
Stage 2: build_network.py     → networks/network_{108..119}.json
Stage 3: build_backbone.py    → backbones/backbone_{108..119}.json
Stage 4: compute_metrics.py   → metrics/metrics_{108..119}.json
Stage 5: build_dashboard.py   → bipartisanship_dashboard.html
```

### Stage 1: Data Collection (`download_bills.py`)

Downloads BILLSTATUS and BILLSUM bulk XML from [GovInfo](https://www.govinfo.gov/bulkdata) for Congresses 108-119. Parses sponsors, cosponsors, policy areas, actions, committees, and more into JSON.

- No API key required, no rate limits
- Supports caching (re-runs only download new/changed data)
- Bill types: HR, S, HJRES, SJRES (binding legislation only)
- `--congress 118` for a single congress or `--start 110 --end 119` for a range

### Stage 2: Network Projection (`build_network.py`)

Projects the bipartite (bill-legislator) network into a one-mode (legislator-legislator) weighted network. Edge weight = number of shared bills. Tracks per-edge policy area counts and computes bipartite degrees for SDSM.

### Stage 3: SDSM Backbone Extraction (`build_backbone.py`)

Applies the Stochastic Degree Sequence Model (Neal, 2014) to test each legislator pair against a null model that preserves activity levels. For each pair, computes expected co-occurrence (`mu = d_i * d_j / B`) and classifies the edge:

- **Positive** — significantly more cosponsorship than expected (cooperation)
- **Negative** — significantly less than expected (avoidance)
- **Neutral** — consistent with chance (discarded)

Uses Benjamini-Hochberg FDR correction across ~145K pairwise tests per congress. Also runs per-policy-area backbone decomposition (min 50 bills per area).

### Stage 4: Metrics & Analysis (`compute_metrics.py`)

Computes on the positive-edge backbone:
- Degree, betweenness, and eigenvector centrality
- Community detection (greedy modularity optimization)
- Per-lawmaker bipartisan score (fraction of positive ties that are cross-party)
- Adjusted Bipartisan Index (ABI = cross-party rate / expected rate)
- Weak polarization (fraction of D-R pairs with no tie) and strong polarization (fraction with negative tie), per Neal (2020)

### Stage 5: Dashboard (`build_dashboard.py`)

Reads metrics JSON and generates a single self-contained HTML file with D3.js and Plotly.js visualizations. The network shows only statistically significant cooperation ties.

## Methodology

See [METHODOLOGY.md](METHODOLOGY.md) for a detailed explanation of the SDSM backbone extraction algorithm, all metrics, and a guide to every chart in the dashboard.

## Data Source

All data comes from the U.S. Government Publishing Office via [GovInfo Bulk Data](https://www.govinfo.gov/bulkdata):

- [BILLSTATUS](https://www.govinfo.gov/bulkdata/BILLSTATUS) — bill metadata, sponsors, cosponsors, actions, committees (Congresses 108-119)
- [BILLSUM](https://www.govinfo.gov/bulkdata/BILLSUM) — CRS bill summaries (Congresses 113-119)

**Dataset characteristics**: ~141,000 bills across 12 Congresses (2003-2027), ~500MB+ of parsed JSON. The data is temporal, spanning 24 years of legislative activity. Each congress contains 8,000-16,500 bills with ~540-560 active legislators.

## Requirements

- Python 3.10+
- `aiohttp` — async HTTP for bulk download
- `networkx` — network analysis and community detection
- `scipy` — normal CDF for SDSM p-values, FDR correction
- `numpy` — numerical computation

