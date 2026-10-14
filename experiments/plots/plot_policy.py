#!/usr/bin/env python3
"""
Figures for E3 (satellite-selection policy comparison).

    python3 plots/plot_policy.py

Produces:
    fig_e3_serving_elevation.png   which satellite serves, and at what elevation
    fig_e3_tradeoff.png            handover rate vs link quality
    fig_e3_summary.png             per-policy metrics side by side
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_style import COLORS, SUMMARY_DIR, apply_style, load_csv, save  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402

POLICIES = ["max_elevation", "elevation_threshold", "max_service_time"]
LABEL = {
    "max_elevation": "Max elevation",
    "elevation_threshold": "Elevation threshold",
    "max_service_time": "Max remaining service time",
}
PCOLOR = {
    "max_elevation": COLORS["arrival"],
    "elevation_threshold": COLORS["psw"],
    "max_service_time": COLORS["total"],
}


def main() -> None:
    apply_style()

    comparison = load_csv(os.path.join(SUMMARY_DIR, "policy_comparison.csv"))
    if not comparison:
        sys.exit("No policy data -- run run/e3_policy_compare.py first.")

    # ── Serving elevation over time ───────────────────────────────────────────
    fig, axes = plt.subplots(len(POLICIES), 1, figsize=(8.0, 7.0), sharex=True)
    for ax, name in zip(axes, POLICIES):
        serving = load_csv(os.path.join(SUMMARY_DIR, f"policy_{name}_serving.csv"))
        hos = load_csv(os.path.join(SUMMARY_DIR, f"policy_{name}_handovers.csv"))
        if not serving:
            continue

        t = np.array([float(r["t_s"]) for r in serving])
        e = np.array([float(r["elevation_deg"]) if r["elevation_deg"] else np.nan
                      for r in serving])
        ax.plot(t - t[0], e, color=PCOLOR[name], linewidth=1.8)

        for h in hos:
            x = float(h["t_s"]) - t[0]
            late = "late" in h["reason"]
            ax.axvline(x, color="#D55E00" if late else "#888888",
                       linestyle="-" if late else "--",
                       linewidth=1.4 if late else 0.9, alpha=0.9 if late else 0.7)

        ax.set_ylabel("Serving\nelevation (deg)")
        ax.set_title(f"{LABEL[name]} — {len(hos)} handovers", loc="left", fontsize=10)
        ax.set_ylim(0, 92)

    axes[-1].set_xlabel("Time (s)")
    fig.suptitle("Serving satellite elevation; vertical lines are handovers "
                 "(orange = late)", y=0.995, fontsize=10)
    save(fig, "fig_e3_serving_elevation.png")

    # ── Trade-off: handover rate vs link quality ──────────────────────────────
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    for row in comparison:
        name = row["policy"]
        ax.scatter(float(row["handovers_per_min"]),
                   float(row["mean_serving_elev_deg"]),
                   s=160, color=PCOLOR.get(name, "#666"), zorder=5,
                   edgecolor="white", linewidth=1.2, label=LABEL.get(name, name))
        ax.annotate(f"  {float(row['mean_serving_one_way_ms']):.2f} ms",
                    (float(row["handovers_per_min"]),
                     float(row["mean_serving_elev_deg"])),
                    textcoords="offset points", xytext=(10, -4), fontsize=8)

    ax.set_xlabel("Handover rate (per minute)")
    ax.set_ylabel("Mean serving elevation (deg)")
    ax.set_title("More handovers buy better links")
    ax.legend(fontsize=8, loc="lower right")
    save(fig, "fig_e3_tradeoff.png")

    # ── Per-policy metric bars ────────────────────────────────────────────────
    metrics = [
        ("handovers", "Handovers"),
        ("late_handovers", "Late handovers"),
        ("mean_serving_elev_deg", "Mean serving\nelevation (deg)"),
        ("mean_serving_one_way_ms", "Mean one-way\ndelay (ms)"),
    ]
    fig, axes = plt.subplots(1, len(metrics), figsize=(12.0, 3.4))
    names = [r["policy"] for r in comparison]
    x = np.arange(len(names))

    for ax, (key, title) in zip(axes, metrics):
        vals = [float(r[key]) for r in comparison]
        ax.bar(x, vals, color=[PCOLOR.get(n, "#666") for n in names])
        ax.set_xticks(x)
        ax.set_xticklabels([LABEL[n].replace(" ", "\n") for n in names], fontsize=7)
        ax.set_title(title, fontsize=9)
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:g}", ha="center", va="bottom", fontsize=8)
        ax.set_ylim(0, max(vals) * 1.25 if max(vals) > 0 else 1)

    fig.suptitle("Policy comparison over a real Starlink pass sequence "
                 "(Montreal)", y=1.04, fontsize=10)
    save(fig, "fig_e3_summary.png")

    print()
    for row in comparison:
        print(f"  {row['policy']:<22} {row['handovers']:>3} HO  "
              f"late {row['late_handovers']:>2}  "
              f"elev {row['mean_serving_elev_deg']:>5} deg  "
              f"delay {row['mean_serving_one_way_ms']:>5} ms")


if __name__ == "__main__":
    main()
