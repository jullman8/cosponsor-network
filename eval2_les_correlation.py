"""
Evaluation 2: Harbridge-Yong effectiveness correlation.

Tests whether our per-member bipartisanship scores predict legislative
effectiveness, replicating Harbridge-Yong/Volden/Wiseman. Uses
the Center for Effective Lawmaking's LES 1.0 (Classic) as the dependent
variable.

Three nested analyses:
  1. Simple correlation: our_raw_bp_rate vs LES, per Congress-chamber.
  2. Benchmark-adjusted: our_raw_bp_rate vs (LES / benchmark).
     The benchmark ratio strips the effects of majority, seniority, chair.
  3. OLS regression: LES ~ our_raw_bp_rate + majority + seniority +
     chair + subcommittee_chair, with robust standard errors. This is
     the Harbridge-Yong-style test controlling for known drivers.

We also run the same three analyses for our_backbone_score (SDSM score)
as a secondary check.

Outputs:
    eval2_outputs/
        merged_long.csv               # joined data, all Congresses
        correlations.csv              # simple + benchmark-adjusted r
        regression_raw.csv            # OLS results, raw score
        regression_backbone.csv       # OLS results, backbone score
        scatter_grid_raw_les.png      # raw_bp_rate vs LES, small multiples
        scatter_grid_raw_benchmark.png # raw_bp_rate vs LES/benchmark

Usage:
    python eval2_les_correlation.py \
        --metrics-dir metrics \
        --cel-house   cel_data/CELHouse93to118-REVISED-06.26.2025.xlsx \
        --cel-senate  cel_data/CELSenate93to118.xls
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import pearsonr, spearmanr

try:
    import statsmodels.api as sm
except ImportError:  # graceful fallback if statsmodels unavailable
    sm = None


# ─── Column mapping (locked from inspect_les_schema.py output) ──────────────

HOUSE_COLS = {
    "bioguide": "Indicator for member in bioguide",
    "congress": "Congress number",
    "les": "LES 1.0",
    "benchmark": "Benchmark score (Classic) based on majority, seniority, chairs",
    "les_over_bench": "LES/benchmark (Classic)",
    "majority": "1 = majority party member",
    "seniority": "Seniority, number of terms served counting current",
    "chair": "1 = committee chair, according to Almanac of American Politics",
    "subchair": "1 = subcommittee chair (or vice chair), according to Almanac of American Politic",
}

SENATE_COLS = {
    "bioguide": "Indicator for member in bioguide",
    "congress": "congress number",
    "les": "LES Classic, not including incorporation in other legislative vehicles",
    "benchmark": "Benchmark score (classic) based on majority, seniority, chairs",
    "les_over_bench": "LES/Benchmark (classic)",
    "majority": "1 if senator is in majority party",
    "seniority": "seniority",
    "chair": "1 if senator is a committee chair",
    "subchair": "1 if senator is a subcommittee chair",
}

# Congresses in our data
CONGRESSES = list(range(108, 119))  # 108-118 inclusive


# ─── Load CEL ───────────────────────────────────────────────────────────────

def load_cel(path: Path, col_map: dict, chamber: str) -> pd.DataFrame:
    """Load a CEL LES file and rename columns to canonical names."""
    df = pd.read_excel(path)
    # Keep only needed columns and the rows in our Congress range
    cols_needed = {v: k for k, v in col_map.items()}
    missing = [c for c in cols_needed if c not in df.columns]
    if missing:
        raise KeyError(f"Missing columns in {path}: {missing}")

    df = df[list(cols_needed.keys())].rename(columns=cols_needed)
    df = df[df["congress"].isin(CONGRESSES)].copy()
    df["chamber"] = chamber
    df["bioguide"] = df["bioguide"].astype(str).str.strip()
    return df


# ─── Load our side ──────────────────────────────────────────────────────────

def load_our_scores(metrics_dir: Path) -> pd.DataFrame:
    rows = []
    for congress in CONGRESSES:
        path = metrics_dir / f"metrics_{congress}.json"
        if not path.exists():
            print(f"  [skip] {path} not found")
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for node in data["nodes"]:
            total_bills = node.get("total_bills", 0) or 0
            bp_bills = node.get("bipartisan_bills", 0) or 0
            raw_rate = bp_bills / total_bills if total_bills > 0 else 0.0
            chamber_val = str(node.get("chamber", "")).lower()
            if chamber_val.startswith("s"):
                chamber = "Senate"
            elif chamber_val.startswith("h"):
                chamber = "House"
            else:
                chamber = "?"
            rows.append({
                "congress": congress,
                "bioguide": str(node["id"]).strip(),
                "chamber": chamber,
                "party": node.get("party", ""),
                "our_raw_bp_rate": raw_rate,
                "our_backbone_score": node.get("bipartisan_score", 0.0),
                "total_bills": total_bills,
                "bipartisan_bills": bp_bills,
                "positive_ties": node.get("positive_ties", 0),
                "bills_sponsored": node.get("bills_sponsored", 0),
            })
    return pd.DataFrame(rows)


# ─── Correlation helpers ────────────────────────────────────────────────────

def compute_corr(df: pd.DataFrame, x_col: str, y_col: str) -> dict:
    sub = df[[x_col, y_col]].dropna()
    if len(sub) < 3:
        return {"n": len(sub), "pearson_r": np.nan, "pearson_p": np.nan,
                "spearman_r": np.nan, "spearman_p": np.nan}
    x = sub[x_col].values.astype(float)
    y = sub[y_col].values.astype(float)
    if np.std(x) == 0 or np.std(y) == 0:
        return {"n": len(sub), "pearson_r": np.nan, "pearson_p": np.nan,
                "spearman_r": np.nan, "spearman_p": np.nan}
    pr, pp = pearsonr(x, y)
    sr, sp = spearmanr(x, y)
    return {"n": len(sub), "pearson_r": pr, "pearson_p": pp,
            "spearman_r": sr, "spearman_p": sp}


# ─── Regression (Harbridge-Yong style) ──────────────────────────────────────

def run_regression(df: pd.DataFrame, score_col: str,
                   chamber: str) -> pd.DataFrame:
    """OLS: LES ~ score + majority + seniority + chair + subchair
    Clustered standard errors by bioguide (member)."""
    if sm is None:
        return pd.DataFrame()

    sub = df[df["chamber"] == chamber].copy()
    sub = sub.dropna(subset=[score_col, "les", "majority", "seniority",
                             "chair", "subchair"])
    if len(sub) < 20:
        return pd.DataFrame()

    X = sub[[score_col, "majority", "seniority", "chair", "subchair"]]
    X = sm.add_constant(X)
    y = sub["les"]

    # Cluster-robust SE by member (bioguide) since members appear in
    # multiple Congresses
    model = sm.OLS(y, X).fit(
        cov_type="cluster",
        cov_kwds={"groups": sub["bioguide"]}
    )

    out = pd.DataFrame({
        "variable": model.params.index,
        "coef": model.params.values,
        "std_err": model.bse.values,
        "t": model.tvalues,
        "p_value": model.pvalues,
        "ci_low": model.conf_int()[0].values,
        "ci_high": model.conf_int()[1].values,
    })
    out.attrs["n"] = int(model.nobs)
    out.attrs["r_squared"] = float(model.rsquared)
    out.attrs["chamber"] = chamber
    return out


# ─── Figures ────────────────────────────────────────────────────────────────

def make_scatter_grid(merged: pd.DataFrame, x_col: str, y_col: str,
                      x_label: str, y_label: str, title: str,
                      outpath: Path) -> None:
    pairs = sorted(merged.dropna(subset=[x_col, y_col])
                   .groupby(["chamber", "congress"]).size().index.tolist())
    if not pairs:
        return

    ncols = 4
    nrows = (len(pairs) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.5 * ncols, 3 * nrows),
                             squeeze=False)
    colors = {"D": "#1f77b4", "R": "#d62728", "I": "#7f7f7f"}

    for i, (chamber, congress) in enumerate(pairs):
        ax = axes[i // ncols][i % ncols]
        sub = merged[(merged["chamber"] == chamber)
                     & (merged["congress"] == congress)].dropna(
                         subset=[x_col, y_col])

        for party, pg in sub.groupby("party"):
            ax.scatter(pg[x_col], pg[y_col],
                       c=colors.get(party, "#999999"),
                       alpha=0.5, edgecolor="white", s=15)

        c = compute_corr(sub, x_col, y_col)
        ax.set_title(f"{congress}th {chamber}\n"
                     f"r={c['pearson_r']:.2f}, n={c['n']}",
                     fontsize=9)
        ax.grid(alpha=0.3)
        if i // ncols == nrows - 1:
            ax.set_xlabel(x_label, fontsize=8)
        if i % ncols == 0:
            ax.set_ylabel(y_label, fontsize=8)

    for j in range(len(pairs), nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")

    fig.suptitle(title, fontsize=12, y=1.00)
    fig.tight_layout()
    fig.savefig(outpath, dpi=130)
    plt.close(fig)


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-dir", type=Path, default=Path("metrics"))
    parser.add_argument("--cel-house", type=Path, required=True)
    parser.add_argument("--cel-senate", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, default=Path("eval2_outputs"))
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    # Load
    print("Loading CEL House...")
    cel_house = load_cel(args.cel_house, HOUSE_COLS, "House")
    print(f"  {len(cel_house)} rows after filtering to Congresses {CONGRESSES[0]}-{CONGRESSES[-1]}")

    print("Loading CEL Senate...")
    cel_senate = load_cel(args.cel_senate, SENATE_COLS, "Senate")
    print(f"  {len(cel_senate)} rows")

    cel = pd.concat([cel_house, cel_senate], ignore_index=True)

    print("Loading our scores from metrics JSON...")
    ours = load_our_scores(args.metrics_dir)
    print(f"  {len(ours)} rows")

    # Join on bioguide + congress (chamber implied by bioguide but we keep
    # it for clarity / sanity)
    merged = cel.merge(
        ours[["congress", "bioguide", "chamber", "party",
              "our_raw_bp_rate", "our_backbone_score",
              "total_bills", "bipartisan_bills", "positive_ties",
              "bills_sponsored"]],
        on=["congress", "bioguide", "chamber"],
        how="inner",
    )
    print(f"\nMatched {len(merged)} rows "
          f"(CEL had {len(cel)}, ours had {len(ours)})")
    if len(merged) < 0.8 * min(len(cel), len(ours)):
        print(f"  WARNING: low match rate. Check bioguide formatting.")

    merged.to_csv(args.outdir / "merged_long.csv", index=False)

    # ── Correlations: simple + benchmark-adjusted ──
    corr_rows = []
    for (chamber, congress), sub in merged.groupby(["chamber", "congress"]):
        for score_col, score_name in [
            ("our_raw_bp_rate", "raw_bp_rate"),
            ("our_backbone_score", "backbone_score"),
        ]:
            simple = compute_corr(sub, score_col, "les")
            adj = compute_corr(sub, score_col, "les_over_bench")
            corr_rows.append({
                "congress": congress,
                "chamber": chamber,
                "score": score_name,
                "n": simple["n"],
                "simple_pearson_r": simple["pearson_r"],
                "simple_spearman_r": simple["spearman_r"],
                "simple_p": simple["pearson_p"],
                "bench_adj_pearson_r": adj["pearson_r"],
                "bench_adj_spearman_r": adj["spearman_r"],
                "bench_adj_p": adj["pearson_p"],
            })

    corr_df = (pd.DataFrame(corr_rows)
               .sort_values(["score", "chamber", "congress"])
               .reset_index(drop=True))
    corr_df.to_csv(args.outdir / "correlations.csv", index=False)

    # ── Regressions (Harbridge-Yong-style) ──
    if sm is not None:
        print("\nRunning OLS regressions with clustered SEs...")
        reg_raw_rows = []
        reg_bb_rows = []
        for chamber in ["House", "Senate"]:
            reg_raw = run_regression(merged, "our_raw_bp_rate", chamber)
            if not reg_raw.empty:
                reg_raw["chamber"] = chamber
                reg_raw["n"] = reg_raw.attrs.get("n")
                reg_raw["r_squared"] = reg_raw.attrs.get("r_squared")
                reg_raw_rows.append(reg_raw)

            reg_bb = run_regression(merged, "our_backbone_score", chamber)
            if not reg_bb.empty:
                reg_bb["chamber"] = chamber
                reg_bb["n"] = reg_bb.attrs.get("n")
                reg_bb["r_squared"] = reg_bb.attrs.get("r_squared")
                reg_bb_rows.append(reg_bb)

        if reg_raw_rows:
            pd.concat(reg_raw_rows, ignore_index=True).to_csv(
                args.outdir / "regression_raw.csv", index=False)
        if reg_bb_rows:
            pd.concat(reg_bb_rows, ignore_index=True).to_csv(
                args.outdir / "regression_backbone.csv", index=False)
    else:
        print("\nstatsmodels not installed; skipping regression. "
              "Install with: pip install statsmodels")

    # ── Figures ──
    make_scatter_grid(
        merged, "our_raw_bp_rate", "les",
        "raw bipartisan rate", "LES 1.0",
        "Raw bipartisan rate vs LES 1.0 — by Congress-chamber",
        args.outdir / "scatter_grid_raw_les.png")
    make_scatter_grid(
        merged, "our_raw_bp_rate", "les_over_bench",
        "raw bipartisan rate", "LES / benchmark",
        "Raw bipartisan rate vs LES/benchmark (adjusted) — by Congress-chamber",
        args.outdir / "scatter_grid_raw_benchmark.png")

    # ── Console summary ──
    print("\n=== Correlations: raw_bp_rate vs LES and LES/benchmark ===")
    raw_tbl = corr_df[corr_df["score"] == "raw_bp_rate"][
        ["congress", "chamber", "n",
         "simple_pearson_r", "bench_adj_pearson_r"]]
    print(raw_tbl.to_string(
        index=False,
        formatters={"simple_pearson_r": "{:.3f}".format,
                    "bench_adj_pearson_r": "{:.3f}".format}))

    print("\n=== Correlations: backbone_score vs LES and LES/benchmark ===")
    bb_tbl = corr_df[corr_df["score"] == "backbone_score"][
        ["congress", "chamber", "n",
         "simple_pearson_r", "bench_adj_pearson_r"]]
    print(bb_tbl.to_string(
        index=False,
        formatters={"simple_pearson_r": "{:.3f}".format,
                    "bench_adj_pearson_r": "{:.3f}".format}))

    # Chamber averages
    print("\n=== Chamber-average correlations (across all Congresses) ===")
    by_ch = (corr_df.groupby(["score", "chamber"])
             [["simple_pearson_r", "bench_adj_pearson_r"]]
             .mean().round(3))
    print(by_ch)

    # Regression headline
    if sm is not None and reg_raw_rows:
        print("\n=== Regression: LES ~ raw_bp_rate + controls ===")
        full = pd.concat(reg_raw_rows, ignore_index=True)
        for chamber in ["House", "Senate"]:
            ch = full[(full["chamber"] == chamber)
                      & (full["variable"] == "our_raw_bp_rate")]
            if not ch.empty:
                row = ch.iloc[0]
                print(f"  {chamber:<7} raw_bp_rate coef = "
                      f"{row['coef']:+.4f} (SE {row['std_err']:.4f}, "
                      f"p={row['p_value']:.3g}), "
                      f"n={row['n']}, R^2={row['r_squared']:.3f}")

    print(f"\nAll outputs in {args.outdir}/")


if __name__ == "__main__":
    main()
