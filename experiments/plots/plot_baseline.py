#!/usr/bin/env python3
"""
Figures for E1 (baseline, no injected delay).

    python3 plots/plot_baseline.py

Produces:
    fig_e1_cdf.png          CDF of the three latency components
    fig_e1_breakdown.png    mean composition of the handover
    fig_e1_interruption.png distribution of the user-plane outage
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_style import (COLORS, FIG_DIR, RAW_DIR, apply_style, fnum,  # noqa: E402
                        load_csv, ok_rows, save)

import matplotlib.pyplot as plt  # noqa: E402


def cdf(ax, values, label, color):
    if not values:
        return
    x = np.sort(np.asarray(values, dtype=float))
    y = np.arange(1, len(x) + 1) / len(x)
    ax.step(x, y, where="post", label=label, color=color, linewidth=1.8)


def main() -> None:
    apply_style()
    rows = ok_rows(load_csv(os.path.join(RAW_DIR, "e1_baseline.csv")))
    if not rows:
        sys.exit("No successful runs in results/raw/e1_baseline.csv "
                 "-- run run/e1_baseline.py first.")

    arrival = fnum(rows, "ue_arrival_ms")
    psw = fnum(rows, "psw_ms")
    total = fnum(rows, "total_ms")
    gap = fnum(rows, "gap_ms")

    # ── CDF ───────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots()
    cdf(ax, arrival, "UE arrival (Xn + RRC + radio switch)", COLORS["arrival"])
    cdf(ax, psw, "Path switch (AMF/SMF/UPF)", COLORS["psw"])
    cdf(ax, total, "Total handover", COLORS["total"])
    ax.set_xlabel("Latency (ms)")
    ax.set_ylabel("Empirical CDF")
    ax.set_title(f"Xn handover latency, no injected delay (n = {len(rows)})")
    ax.set_ylim(0, 1.02)
    ax.legend(loc="lower right")
    save(fig, "fig_e1_cdf.png")

    # ── Breakdown ─────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(5.2, 4.0))
    m_arr, m_psw = float(np.mean(arrival)), float(np.mean(psw))
    ax.bar(["Handover"], [m_arr], color=COLORS["arrival"],
           label=f"UE arrival ({m_arr:.1f} ms)")
    ax.bar(["Handover"], [m_psw], bottom=[m_arr], color=COLORS["psw"],
           label=f"Path switch ({m_psw:.1f} ms)")
    ax.set_ylabel("Mean latency (ms)")
    ax.set_title("Where the handover time is spent")
    ax.legend()
    save(fig, "fig_e1_breakdown.png")

    # ── Interruption ──────────────────────────────────────────────────────────
    if any(g > 0 for g in gap):
        fig, ax = plt.subplots()
        bins = np.arange(0, max(gap) + 10, 10)
        ax.hist(gap, bins=bins, color=COLORS["gap"], edgecolor="white")
        ax.set_xlabel("User-plane interruption (ms)")
        ax.set_ylabel("Handovers")
        ax.set_title(f"Interruption, no injected delay "
                     f"(median {np.median(gap):.0f} ms, n = {len(gap)})")
        save(fig, "fig_e1_interruption.png")
    else:
        print("  (no measurable interruption -- skipping interruption figure)")

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n  n = {len(rows)} successful handovers")
    for name, vals in (("ue_arrival_ms", arrival), ("psw_ms", psw),
                       ("total_ms", total), ("gap_ms", gap)):
        if vals:
            a = np.asarray(vals)
            print(f"  {name:>14}: mean {a.mean():7.2f}  median {np.median(a):7.2f}  "
                  f"p95 {np.percentile(a, 95):7.2f}  max {a.max():7.2f}")


if __name__ == "__main__":
    main()
