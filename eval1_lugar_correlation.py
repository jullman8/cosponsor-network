"""
Evaluation 1: External validation against the Lugar Center Bipartisan Index.

We compare TWO per-member bipartisanship scores against Lugar's BPI:

  1. raw_bp_rate  = bipartisan_bills / total_bills  [primary]
     This is the proportion of a member's bills that involve at least one
     opposite-party cosponsor. It is a direct analog of Harbridge-Yong's
     "proportion of cosponsors attracted" and of what Lugar measures.
     This is the appropriate score for external validation.

  2. backbone_score = cross_party_positive_ties / positive_ties  [secondary]
     This is computed on the SDSM-pruned network; it captures only
     statistically significant cross-party cooperation ties. It is a
     stricter measure and is expected to correlate less strongly with
     Lugar's raw-count index by design.

Reporting both. Our system contains the raw score as a baseline (validating 
against Lugar) plus the SDSM-adjusted score as a novel stricter measure.

Outputs:
    eval1_outputs/
        merged_long.csv
        unmatched.csv
        correlations.csv             # both scores, each Congress-chamber
        scatter_grid_raw.png         # raw_bp_rate vs Lugar (primary)
        scatter_grid_backbone.png    # backbone_score vs Lugar (secondary)
        top_divergences_raw.csv      # where raw_bp_rate disagrees with Lugar
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import pearsonr, spearmanr


# ─── Name normalization ─────────────────────────────────────────────────────

NICKNAME_MAP = {
    "christopher": "chris", "michael": "mike", "robert": "bob",
    "richard": "rick", "william": "bill", "thomas": "tom",
    "james": "jim", "edward": "ed", "benjamin": "ben",
    "anthony": "tony", "daniel": "dan", "matthew": "matt",
    "joseph": "joe", "samuel": "sam", "charles": "chuck",
    "nicholas": "nick", "kenneth": "ken", "frederick": "fred",
    "raymond": "ray", "gerald": "gerry", "patrick": "pat",
    "theodore": "ted", "timothy": "tim", "lawrence": "larry",
    "douglas": "doug", "alexander": "alex", "jonathan": "jon",
    "steven": "steve", "stephen": "steve", "gregory": "greg",
    "andrew": "andy", "cynthia": "cindy", "katherine": "kate",
    "kathleen": "kathy", "margaret": "maggie", "elizabeth": "liz",
    "deborah": "debbie", "patricia": "patty", "barbara": "barb",
    "amanda": "mandy",
}


def normalize_name(name: str) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    replacements = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
                    "ñ": "n", "ü": "u", "ç": "c"}
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def nickname_equiv(a: str, b: str) -> bool:
    a, b = normalize_name(a), normalize_name(b)
    if a == b:
        return True
    if NICKNAME_MAP.get(a) == b or NICKNAME_MAP.get(b) == a:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


# ─── Name parsing ───────────────────────────────────────────────────────────

def parse_split_name(full: str) -> tuple[str, str]:
    """Parse GovInfo names like 'Rep. Camp, Dave [R-MI-4]'."""
    if not full:
        return "", ""
    s = full.strip()
    s = re.sub(r"^(Rep\.|Sen\.|Del\.|Res\.|Commish\.)\s+", "", s,
               flags=re.IGNORECASE)
    s = re.sub(r"\s*\[[^\]]*\]\s*$", "", s).strip()
    if "," in s:
        last, rest = s.split(",", 1)
        first = rest.strip().split()[0] if rest.strip() else ""
        return first, last.strip()
    parts = s.split()
    return (parts[0], parts[-1]) if len(parts) > 1 else ("", parts[0])


def load_our_scores(metrics_dir: Path, congress: int) -> pd.DataFrame:
    path = metrics_dir / f"metrics_{congress}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for node in data["nodes"]:
        first, last = parse_split_name(node.get("name", ""))
        total_bills = node.get("total_bills", 0) or 0
        bp_bills = node.get("bipartisan_bills", 0) or 0
        raw_rate = bp_bills / total_bills if total_bills > 0 else 0.0

        rows.append({
            "congress": congress,
            "bioguide_id": node["id"],
            "raw_name": node.get("name", ""),
            "first_name": first,
            "last_name": last,
            "state": node.get("state", "").upper(),
            "party": node.get("party", ""),
            "chamber": node.get("chamber", ""),
            # Primary score: raw proportion (Harbridge-Yong style)
            "our_raw_bp_rate": raw_rate,
            # Secondary score: SDSM backbone proportion
            "our_backbone_score": node.get("bipartisan_score", 0.0),
            "total_bills": total_bills,
            "bipartisan_bills": bp_bills,
            "positive_ties": node.get("positive_ties", 0),
            "negative_ties": node.get("negative_ties", 0),
            "cross_party_positive": node.get("cross_party_positive", 0),
            "bills_sponsored": node.get("bills_sponsored", 0),
        })
    return pd.DataFrame(rows)


def load_lugar(lugar_dir: Path, congress: int, chamber: str) -> pd.DataFrame:
    path = lugar_dir / f"lugar_{congress}_{chamber.lower()}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["congress"] = congress
    df["chamber"] = chamber
    return df


# ─── Matching ───────────────────────────────────────────────────────────────

def match_members(ours: pd.DataFrame, lugar: pd.DataFrame,
                  chamber: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    ours_ch = ours[ours["chamber"].str.lower()
                   .str.startswith(chamber[0].lower())].copy()

    idx = defaultdict(list)
    for _, row in ours_ch.iterrows():
        key = (normalize_name(row["last_name"]), row["state"], row["party"])
        idx[key].append(row)

    matched_rows = []
    unmatched = []
    for _, lrow in lugar.iterrows():
        key = (normalize_name(lrow["last_name"]), lrow["state"], lrow["party"])
        candidates = idx.get(key, [])

        pick = None
        if len(candidates) == 1:
            pick = candidates[0]
        elif len(candidates) > 1:
            for c in candidates:
                if nickname_equiv(c["first_name"], lrow["first_name"]):
                    pick = c
                    break

        if pick is None:
            unmatched.append(lrow.to_dict())
            continue

        matched_rows.append({
            "congress": lrow["congress"],
            "chamber": chamber,
            "bioguide_id": pick["bioguide_id"],
            "name": f"{lrow['first_name']} {lrow['last_name']}",
            "state": lrow["state"],
            "party": lrow["party"],
            "lugar_score": lrow["lugar_score"],
            "lugar_rank": lrow["rank"],
            "our_raw_bp_rate": pick["our_raw_bp_rate"],
            "our_backbone_score": pick["our_backbone_score"],
            "positive_ties": pick["positive_ties"],
            "cross_party_positive": pick["cross_party_positive"],
            "bills_sponsored": pick["bills_sponsored"],
            "total_bills": pick["total_bills"],
            "bipartisan_bills": pick["bipartisan_bills"],
        })

    return pd.DataFrame(matched_rows), pd.DataFrame(unmatched)


# ─── Correlations ──────────────────────────────────────────────────────────

def compute_corr(df: pd.DataFrame, score_col: str) -> dict:
    if len(df) < 3:
        return {"n": len(df), "pearson_r": np.nan, "pearson_p": np.nan,
                "spearman_r": np.nan, "spearman_p": np.nan}
    x = df[score_col].values.astype(float)
    y = df["lugar_score"].values.astype(float)
    if np.std(x) == 0 or np.std(y) == 0:
        return {"n": len(df), "pearson_r": np.nan, "pearson_p": np.nan,
                "spearman_r": np.nan, "spearman_p": np.nan}
    pr, pp = pearsonr(x, y)
    sr, sp = spearmanr(x, y)
    return {"n": len(df), "pearson_r": pr, "pearson_p": pp,
            "spearman_r": sr, "spearman_p": sp}


# ─── Figures ────────────────────────────────────────────────────────────────

def make_scatter_grid(all_merged: pd.DataFrame, corrs: list[dict],
                      score_col: str, x_label: str, outpath: Path,
                      title: str) -> None:
    pairs = [(c["congress"], c["chamber"]) for c in corrs if c["n"] >= 3]
    if not pairs:
        return
    chambers_present = sorted({ch for _, ch in pairs})
    congresses_present = sorted({c for c, _ in pairs})
    nrows = len(chambers_present)
    ncols = len(congresses_present)
    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(2.8 * ncols, 2.6 * nrows),
                             squeeze=False)
    colors = {"D": "#1f77b4", "R": "#d62728", "I": "#7f7f7f"}

    for r, chamber in enumerate(chambers_present):
        for c, congress in enumerate(congresses_present):
            ax = axes[r][c]
            if (congress, chamber) not in pairs:
                ax.axis("off")
                continue
            sub = all_merged[(all_merged["congress"] == congress)
                             & (all_merged["chamber"] == chamber)]
            for party, pg in sub.groupby("party"):
                ax.scatter(pg[score_col], pg["lugar_score"],
                           c=colors.get(party, "#999999"), alpha=0.5,
                           edgecolor="white", s=15)
            corr = next(x for x in corrs if x["congress"] == congress
                        and x["chamber"] == chamber)
            ax.set_title(f"{congress}th {chamber}  r={corr['pearson_r']:.2f}, "
                         f"\u03c1={corr['spearman_r']:.2f}, n={corr['n']}",
                         fontsize=9)
            ax.axhline(0, color="#cccccc", lw=0.5)
            ax.grid(alpha=0.3)
            if r == nrows - 1:
                ax.set_xlabel(x_label, fontsize=8)
            if c == 0:
                ax.set_ylabel("Lugar BPI", fontsize=8)
            ax.tick_params(labelsize=7)

    fig.suptitle(title, fontsize=12, y=1.00)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def top_divergences(df: pd.DataFrame, score_col: str,
                    n: int = 30) -> pd.DataFrame:
    out = []
    for (congress, chamber), sub in df.groupby(["congress", "chamber"]):
        sub = sub.copy()
        sub["our_pct"] = sub[score_col].rank(pct=True)
        sub["lugar_pct"] = sub["lugar_score"].rank(pct=True)
        sub["pct_gap"] = sub["our_pct"] - sub["lugar_pct"]
        out.append(sub)
    if not out:
        return pd.DataFrame()
    all_df = pd.concat(out, ignore_index=True)
    all_df["abs_gap"] = all_df["pct_gap"].abs()
    return (all_df.sort_values("abs_gap", ascending=False)
            .head(n)[["congress", "chamber", "name", "state", "party",
                      score_col, "lugar_score",
                      "bills_sponsored", "total_bills",
                      "our_pct", "lugar_pct", "pct_gap"]])


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-dir", type=Path, default=Path("metrics"))
    parser.add_argument("--lugar-dir", type=Path, default=Path("lugar_data"))
    parser.add_argument("--outdir", type=Path, default=Path("eval1_outputs"))
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    # 113 excluded: Lugar's 113 pages use a swapped column schema.
    congresses = [114, 115, 116, 117, 118]
    chambers = ["Senate", "House"]

    all_merged = []
    all_unmatched = []

    for congress in congresses:
        metrics_path = args.metrics_dir / f"metrics_{congress}.json"
        if not metrics_path.exists():
            print(f"[skip] {metrics_path} not found")
            continue
        ours = load_our_scores(args.metrics_dir, congress)

        for chamber in chambers:
            lugar = load_lugar(args.lugar_dir, congress, chamber)
            if lugar.empty:
                continue

            merged, unmatched = match_members(ours, lugar, chamber)
            print(f"{congress}th {chamber}: matched "
                  f"{len(merged)}/{len(lugar)} "
                  f"({len(merged)/len(lugar):.1%})")

            if not merged.empty:
                all_merged.append(merged)
            if not unmatched.empty:
                u = unmatched.copy()
                u["congress"] = congress
                u["chamber"] = chamber
                all_unmatched.append(u)

    if not all_merged:
        print("No matches produced.")
        return

    full = pd.concat(all_merged, ignore_index=True)
    full.to_csv(args.outdir / "merged_long.csv", index=False)

    # ── Correlations: both scores, each Congress-chamber ──
    corr_rows = []
    raw_corrs = []
    bb_corrs = []
    for (congress, chamber), sub in full.groupby(["congress", "chamber"]):
        raw_c = compute_corr(sub, "our_raw_bp_rate")
        bb_c = compute_corr(sub, "our_backbone_score")

        raw_c_tagged = {**raw_c, "congress": congress, "chamber": chamber}
        bb_c_tagged = {**bb_c, "congress": congress, "chamber": chamber}
        raw_corrs.append(raw_c_tagged)
        bb_corrs.append(bb_c_tagged)

        corr_rows.append({
            "congress": congress,
            "chamber": chamber,
            "n": raw_c["n"],
            "raw_pearson_r": raw_c["pearson_r"],
            "raw_spearman_r": raw_c["spearman_r"],
            "raw_pearson_p": raw_c["pearson_p"],
            "backbone_pearson_r": bb_c["pearson_r"],
            "backbone_spearman_r": bb_c["spearman_r"],
            "backbone_pearson_p": bb_c["pearson_p"],
        })

    corr_df = (pd.DataFrame(corr_rows)
               .sort_values(["chamber", "congress"])
               .reset_index(drop=True))
    corr_df.to_csv(args.outdir / "correlations.csv", index=False)

    # ── Figures ──
    make_scatter_grid(
        full, raw_corrs, "our_raw_bp_rate",
        "Our raw bipartisan rate (bp_bills / total_bills)",
        args.outdir / "scatter_grid_raw.png",
        f"Raw proportion score vs Lugar BPI (n={len(full)})")
    make_scatter_grid(
        full, bb_corrs, "our_backbone_score",
        "Our SDSM-backbone score",
        args.outdir / "scatter_grid_backbone.png",
        f"SDSM-backbone score vs Lugar BPI (n={len(full)})")

    div_raw = top_divergences(full, "our_raw_bp_rate", n=30)
    div_raw.to_csv(args.outdir / "top_divergences_raw.csv", index=False)

    if all_unmatched:
        u_all = pd.concat(all_unmatched, ignore_index=True)
        u_all.to_csv(args.outdir / "unmatched.csv", index=False)

    # ── Console summary ──
    print("\n=== Correlations (both scores vs Lugar BPI) ===")
    display = corr_df[["congress", "chamber", "n",
                       "raw_pearson_r", "raw_spearman_r",
                       "backbone_pearson_r", "backbone_spearman_r"]]
    print(display.to_string(
        index=False,
        formatters={"raw_pearson_r": "{:.3f}".format,
                    "raw_spearman_r": "{:.3f}".format,
                    "backbone_pearson_r": "{:.3f}".format,
                    "backbone_spearman_r": "{:.3f}".format}))

    # Summary: average across Congresses by chamber
    print("\n=== Average across 5 Congresses ===")
    by_ch = (corr_df.groupby("chamber")
             [["raw_pearson_r", "raw_spearman_r",
               "backbone_pearson_r", "backbone_spearman_r"]]
             .mean())
    print(by_ch.to_string(
        formatters={"raw_pearson_r": "{:.3f}".format,
                    "raw_spearman_r": "{:.3f}".format,
                    "backbone_pearson_r": "{:.3f}".format,
                    "backbone_spearman_r": "{:.3f}".format}))

    print(f"\nAll outputs in {args.outdir}/")


if __name__ == "__main__":
    main()
