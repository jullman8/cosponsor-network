# Methodology: Quantifying Congressional Bipartisanship

This document explains the analytical pipeline in detail, covering why each step is necessary and how the core algorithms work.

## 1. The Data

We download bill metadata from the U.S. Government Publishing Office's [GovInfo Bulk Data](https://www.govinfo.gov/bulkdata) service. This covers Congresses 108-119 (2003-2027), roughly 8,000-16,000 bills per congress.

For each bill we extract:
- **Sponsor** and **cosponsors** (with party, state, chamber)
- **Policy area** (CRS classification, e.g., Health, Defense, Education)
- **Bill type** (HR, S, HJRES, SJRES — binding legislation only)

We use cosponsorship rather than roll-call votes for two reasons. First, cosponsorship captures **pre-floor collaboration** that roll calls miss. Party leaders control which bills reach the floor, so roll-call data can overstate polarization by excluding legislation that would have drawn bipartisan support (Lee, 2009). Second, Kessler and Krehbiel (1996) established that cosponsorship is a meaningful form of **legislative signaling** — not mere position-taking — making co-occurrence patterns a valid behavioral measure. Alemán (2009) confirmed this by showing that cosponsorship-based ideal points diverge meaningfully from roll-call-based ones, indicating the two data sources capture different dimensions of legislative behavior.

## 2. The Bipartite Projection Problem

The raw data is **bipartite**: bills on one side, legislators on the other. A legislator connects to a bill by sponsoring or cosponsoring it.

```
Legislator A ──── Bill 1 ──── Legislator B
                  Bill 2 ──── Legislator B
Legislator A ──── Bill 3 ──── Legislator C
```

To study legislator-to-legislator relationships, we **project** this into a one-mode network: two legislators share an edge weighted by how many bills they both appear on.

```
A ──(2)── B      (A and B share 2 bills)
A ──(1)── C      (A and C share 1 bill)
```

To manage density during projection, bills with more than 15 participants (sponsor + cosponsors) use a **star topology**: edges connect only the sponsor to each cosponsor, rather than all pairwise combinations. This prevents omnibus bills with 50+ cosponsors from generating 1,225+ edges that would dominate the network. Bills with 15 or fewer participants use all pairwise combinations, which more accurately reflects the collaborative relationships on smaller bills.

Even with this mitigation, the projected network is **artificially dense**. Neal (2014) showed this is inherent to bipartite projections: a senator who cosponsors 500 bills will share edges with hundreds of colleagues — not because of meaningful relationships, but because they are prolific. The resulting network is a hairball where almost everyone is connected to everyone, making it impossible to distinguish real collaboration from noise. Fowler (2006) mapped cosponsorship networks from 1973-2004 using raw projections, introducing useful connectedness metrics but without addressing this density problem.

## 3. The SDSM Solution

The Stochastic Degree Sequence Model (Neal, 2014) solves this by asking a simple question for every pair of legislators:

> Given how active each of these two legislators is, how many bills would they share **by chance alone**?

### The Null Model

Consider legislators A and B in a congress with B total bills (counting only bills that have at least one cosponsor). Legislator A appears on d_A bills; legislator B appears on d_B bills. If bill participation were random (preserving each legislator's total activity), the probability that both A and B appear on any given bill is approximately:

```
p = (d_A / B) * (d_B / B)
```

Over B bills, the expected number of shared bills is:

```
mu = d_A * d_B / B
```

with variance:

```
sigma^2 = mu * (1 - mu / B)
```

For large B (typically 6,000-13,000 bills with cosponsors per congress), this is well-approximated by a normal distribution.

### Example

Suppose Senator X appears on 300 bills and Senator Y appears on 200 bills, out of 10,000 total. The null model predicts:

```
mu = 300 * 200 / 10,000 = 6 shared bills
```

- If they actually share **25 bills**, that is far above the baseline. This excess cooperation is statistically significant — it represents a real collaborative relationship.
- If they share **1 bill**, that is far below. Given their activity levels, they should overlap more. This deficit is statistically significant — it suggests avoidance.
- If they share **5 or 7 bills**, that is right around what chance predicts. No meaningful signal.

### Statistical Testing

For each pair, we compute a z-score:

```
z = (observed - mu) / sigma
```

And derive one-tailed p-values:
- **p_upper**: probability of observing this many or more shared bills under the null (tests for cooperation)
- **p_lower**: probability of observing this few or fewer (tests for avoidance)

### Multiple Testing Correction

A typical congress has ~540 legislators, producing roughly 145,000 pairwise tests. At a 5% significance level without correction, we would expect ~7,250 false positives per tail. We apply the **Benjamini-Hochberg False Discovery Rate (FDR) correction** to control the proportion of false discoveries at 5%.

### Edge Classification

After correction, each pair is classified:
- **Positive edge**: significantly more cosponsorship than expected (p_upper < 0.05 after FDR) — genuine cooperation
- **Negative edge**: significantly less than expected (p_lower < 0.05 after FDR) — avoidance
- **Neutral**: consistent with chance — discarded from the backbone

In practice, the backbone is dramatically sparser than the raw projection. For the 118th Congress: 63,595 raw edges collapse to 2,490 positive and 24,115 negative edges. Over 96% of "cooperation" in the raw network was noise.

## 4. Policy-Area Decomposition

After computing the overall backbone, we repeat the SDSM process **per policy area** (e.g., Health, Defense, Education). For each area:

1. Restrict to bills in that policy area
2. Recompute bipartite degrees within the area (how many area-specific bills each legislator appears on)
3. Recompute expected co-occurrence using the area-specific bill count as B
4. Run the same significance test with FDR correction

Areas with fewer than 50 bills are skipped (insufficient statistical power). This decomposition reveals whether bipartisanship is domain-specific. For example, Health policy might show strong cross-party cooperation while Taxation does not.

## 5. Metrics Computed on the Backbone

All downstream metrics operate on the **positive-edge backbone** (the cooperation network), not the raw projection.

### Adjusted Bipartisan Index (ABI)

```
ABI = raw_bipartisan_rate / expected_cross_party_rate
```

Where:
- **raw_bipartisan_rate** = fraction of positive backbone edges that cross party lines
- **expected_cross_party_rate** = `1 - (p_D^2 + p_R^2 + p_Other^2)`, the probability that a random pair of legislators would be from different parties

ABI = 1.0 means bipartisanship matches what random party mixing would predict. ABI < 1.0 means less bipartisan cooperation than expected; ABI > 1.0 means more. This normalization accounts for changing party balance across congresses.

### Polarization Metrics (Neal, 2020)

The signed backbone enables two distinct polarization measures:

- **Weak polarization** = fraction of possible D-R pairs with no significant tie (neutral). These pairs simply do not interact in a statistically meaningful way.
- **Strong polarization** = fraction of possible D-R pairs with a **negative** tie. These pairs actively avoid working together beyond what chance would predict.

The denominator is `|D| * |R|` (total possible Democrat-Republican pairs). This distinction matters: a congress where parties simply ignore each other (high weak polarization) is qualitatively different from one where they actively avoid each other (high strong polarization).

### Centrality Metrics

Computed on the positive-edge backbone using NetworkX:

- **Degree centrality** — fraction of possible ties that a legislator has. Higher = more collaborative partners.
- **Betweenness centrality** — how often a legislator lies on the shortest path between others. High betweenness indicates a bridge between groups. This is the centrality measure surfaced in the dashboard's "Highest Centrality" table.
- **Eigenvector centrality** — a legislator is central if their connections are also central. Measures influence through network position. This metric is computed and stored in the JSON output for research use but is not displayed in the dashboard (it correlates highly with degree centrality on sparse backbone networks).

### Per-Lawmaker Bipartisan Score

For each legislator: the fraction of their positive backbone ties that cross party lines. This adapts the proportion metric from Harbridge-Yong et al. (2023), who showed that the fraction of opposite-party cosponsors predicts legislative effectiveness. Our improvement is computing this on the SDSM backbone rather than raw co-occurrence, so the score reflects statistically significant cooperation ties rather than incidental overlaps. Rippere (2016) found that bipartisan cosponsorship persists even in highly polarized congresses — our per-lawmaker scores confirm this, showing some legislators consistently maintain high cross-party cooperation regardless of the overall trend.

### Community Detection

Greedy modularity optimization (Clauset-Newman-Moore algorithm via NetworkX) partitions the backbone into communities — clusters of legislators who cooperate significantly with each other, following the approach of Zhang et al. (2008), who applied modularity-based community detection to cosponsorship networks and found that communities strongly align with party structure.

We also compute **party modularity** using the known D/R/Other partition, measuring how well party labels predict network structure. When party modularity is high, the backbone splits cleanly along party lines — most cooperation happens within-party. When detected communities differ from the party partition, it reveals subgroups that cross party lines or fractures within a party. The dashboard displays each community's party composition so users can see whether clusters are partisan or bipartisan.

## 6. Dashboard Guide

The dashboard has two tabs: **Trends Over Time** (cross-congress charts) and **Per-Congress Detail** (network graph and lawmaker tables for a single congress).

### Trends Over Time

#### Adjusted Bipartisan Index (ABI) Over Time

The central chart. ABI measures cross-party cooperation on the SDSM backbone, normalized for party composition:

```
ABI = observed cross-party cooperation rate / expected rate under random mixing
```

ABI = 1.0 means cooperation matches what you would see if legislators cosponsored randomly without regard to party. Below 1.0 means less cooperation than expected — the lower the value, the more partisan the congress. A dropdown lets you view ABI for a specific policy area (e.g., Health, Defense), using the per-area SDSM backbone from Section 4.

#### Polarization Trends

Two lines tracking distinct forms of polarization across all possible Democrat-Republican legislator pairs:

- **Weak polarization**: the fraction of D-R pairs with no statistically significant cosponsorship tie. These legislators simply don't interact — they exist in separate legislative worlds.
- **Strong polarization**: the fraction of D-R pairs who cosponsor together significantly *less* than their activity levels predict. This is active avoidance, not just absence.

Rising weak polarization means the parties are sorting apart. Rising strong polarization means they are actively repelling. A congress can have high weak polarization (parties ignoring each other) without high strong polarization (no active hostility), or vice versa.

#### Raw vs Expected Bipartisan Rates

Shows the two components that ABI is calculated from:

- **Raw rate**: the actual fraction of positive backbone edges that cross party lines.
- **Expected rate**: the fraction that *would* cross party lines if cooperation were random, determined solely by party balance (e.g., a 50/50 congress expects ~50% cross-party ties).

This chart answers *why* ABI changes. If the raw rate drops while the expected rate stays flat, actual cooperation declined. If both drop together, the shift may be driven by party composition rather than behavior.

#### Party Composition Over Time

A stacked bar showing how many Democrats, Republicans, and Independents served in each congress. This is context for everything above — party balance directly affects the expected cross-party rate, which in turn affects ABI. A congress with a 60/40 split has a different baseline than one with 50/50.

#### Bipartisanship by Policy Area Over Time (Heatmap)

A heatmap where each cell shows the ABI for a specific policy area in a specific congress, computed using per-area SDSM backbone extraction (Section 4). Brighter colors indicate more bipartisan cooperation than expected in that domain. Gray cells indicate fewer than 50 bills in that area for that congress — too few for reliable statistical testing.

This reveals whether bipartisanship is domain-specific. For instance, Veterans' Affairs or Natural Resources might show consistent cross-party cooperation while Taxation or Immigration may not.

#### Most Bipartisan Lawmakers by Congress

The top 5 lawmakers per congress, ranked by bipartisan score (minimum 10 bills to qualify). The score is the fraction of a lawmaker's statistically significant cooperation ties that cross party lines. A score of 50% means half their backbone connections are with the opposing party. This gives a human-readable sanity check on the aggregate metrics — do the names match known bipartisan figures?

### Per-Congress Detail

#### Backbone Cosponsor Network

An interactive D3.js force-directed graph showing the positive-edge backbone for a selected congress. Each node is a legislator; each line is a statistically significant cooperation tie (SDSM positive edge).

- **Node color**: blue = Democrat, red = Republican, purple = Independent
- **Node size**: proportional to degree centrality (more cooperation ties = larger node)
- **Gold lines**: cross-party cooperation ties
- **Gray lines**: same-party cooperation ties

Negative edges (avoidance) are not drawn — they represent the absence of a relationship, not a connection. Their aggregate effect is captured by the Polarization Trends chart instead.

**Filters**: Show all cooperation ties or cross-party only. Filter by party, chamber (House/Senate), state, or policy area. Click any node to open a detail panel with that lawmaker's ego network, top cooperating colleagues, and policy breakdown. Search by name.

#### Bipartisan Score (Table)

All legislators in the selected congress, ranked from most to least bipartisan. The score is the fraction of their positive backbone ties that cross party lines. Scrollable — you can see who ranks at the bottom as well as the top.

#### Highest Centrality (Table)

All legislators ranked by betweenness centrality on the positive backbone. High betweenness means a lawmaker frequently lies on the shortest path between other legislators in the cooperation network — they serve as bridges between groups that would otherwise be disconnected.

#### Additional Per-Congress Charts

- **Bipartisanship by Party**: average bipartisan score by party for the selected congress.
- **Bipartisanship by Policy Area (ABI)**: bar chart of per-area ABI for the selected congress.
- **Bipartisanship by State**: average bipartisan score by state.
- **Detected Communities**: clusters of legislators identified by modularity optimization, showing party composition of each cluster.

## 7. Key Results

Across Congresses 108-119, the pipeline reveals:

| Metric | 108th (2003) | 118th (2023) | Trend |
|--------|:---:|:---:|---|
| ABI | 0.358 | 0.183 | Declining — less bipartisan cooperation on the backbone |
| Weak polarization | 79.0% | 86.1% | Rising — more D-R pairs with no significant tie |
| Strong polarization | 20.8% | 13.7% | Slightly declining |
| Positive backbone edges | 817 | 2,490 | More significant ties overall (larger congress, more bills) |

The ABI decline from ~0.36 to ~0.18 indicates that cross-party cooperation on the backbone has roughly halved relative to what party composition would predict. This trend is consistent with Andris et al. (2015), who demonstrated declining cross-party cooperation in the House using roll-call vote networks. Our results extend their finding to cosponsorship-based backbone networks, confirming the pattern holds in pre-floor collaboration as well. Notably, cooperation never reaches zero — consistent with Rippere (2016), who found bipartisan cosponsorship persists even in highly polarized congresses.

## References

- Alemán, E. (2009). Comparing cosponsorship and roll-call ideal points. *Legislative Studies Quarterly*, 34(1), 87-116.
- Andris, C., Lee, D., Hamilton, M. J., Martino, M., Gunning, C. E., & Selden, J. A. (2015). The rise of partisanship and super-cooperators in the U.S. House of Representatives. *PLOS ONE*, 10(4), e0123507.
- Fowler, J. H. (2006). Connecting the Congress: A study of cosponsorship networks. *Political Analysis*, 14(4), 456-487.
- Harbridge-Yong, L., Volden, C., & Wiseman, A. E. (2023). The bipartisan path to effective lawmaking. *The Journal of Politics*, 85(3), 1048-1063.
- Kessler, D., & Krehbiel, K. (1996). Dynamics of cosponsorship. *American Political Science Review*, 90(3), 555-566.
- Lee, F. E. (2009). *Beyond ideology: Politics, principles, and partisanship in the U.S. Senate*. University of Chicago Press.
- Neal, Z. P. (2014). The backbone of bipartite projections: Inferring relationships from co-authorship, co-sponsorship, co-attendance and other co-behaviors. *Social Networks*, 39, 84-97.
- Neal, Z. P. (2020). A sign of the times? Weak and strong polarization in the U.S. Congress, 1973-2016. *Social Networks*, 60, 103-112.
- Rippere, P. S. (2016). Polarization reconsidered: Bipartisan cooperation through bill cosponsorship. *Polity*, 48(2), 243-278.
- Zhang, Y., Friend, A. J., Traud, A. L., Porter, M. A., Fowler, J. H., & Mucha, P. J. (2008). Community structure in Congressional cosponsorship networks. *Physica A*, 387(7), 1705-1712.
