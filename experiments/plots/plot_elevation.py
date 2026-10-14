#!/usr/bin/env python3
"""
Figures for E2 (handover performance vs satellite elevation angle).

    python3 plots/plot_elevation.py                  # every scenario found
    python3 plots/plot_elevation.py regenerative

Produces, per scenario:
    fig_e2_<scenario>_latency.png        latency components vs elevation
    fig_e2_<scenario>_interruption.png   user-plane outage vs elevation
and, when more than one scenario has data:
    fig_e2_comparison.png                architectures side by side
    fig_e2_sensitivity.png               ms of handover cost per ms of link delay

Note on the x axis: elevation DECREASES to the right, so the axis runs from the
satellite overhead (short link, low delay) toward the horizon (long link, high
delay). Reading left to right therefore follows a satellite setting.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_style import (COLORS, RAW_DIR, SCENARIO_LABEL, apply_style,  # noqa: E402
                        fnum, load_csv, ok_rows, save)

import matplotlib.pyplot as plt  # noqa: E402

SCENARIOS = ["transparent", "regenerative", "isl"]


def group_by_elevation(rows: list[dict]) -> dict[float, list[dict]]:
    g: dict[float, list[dict]] = defaultdict(list)
    for r in rows:
        g[round(float(r["elevation_deg"]), 1)].append(r)
    return dict(sorted(g.items(), reverse=True))   # 90 deg first


def stats(rows: list[dict], key: str) -> tuple[float, float]:
    v = fnum(rows, key)
    if not v:
        return float("nan"), 0.0
    a = np.asarray(v)
    return float(a.mean()), float(a.std())


def plot_scenario(scenario: str, rows: list[dict]) -> None:
    grouped = group_by_elevation(rows)
    elevs = list(grouped.keys())
    labels = [f"{e:.0f}" for e in elevs]
    x = np.arange(len(elevs))

    rtt = [float(grouped[e][0]["rtt_ms"]) for e in elevs]

    # ── Latency components ────────────────────────────────────────────────────
    fig, ax = plt.subplots()
    for key, label, color in (
            ("ue_arrival_ms", "UE arrival", COLORS["arrival"]),
            ("psw_ms", "Path switch", COLORS["psw"]),
            ("total_ms", "Total", COLORS["total"])):
        means = [stats(grouped[e], key)[0] for e in elevs]
        errs = [stats(grouped[e], key)[1] for e in elevs]
        ax.errorbar(x, means, yerr=errs, marker="o", capsize=3,
                    label=label, color=color, linewidth=1.8, markersize=5)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Satellite elevation angle (deg)   [overhead -> horizon]")
    ax.set_ylabel("Handover latency (ms)")
    ax.set_title(SCENARIO_LABEL.get(scenario, scenario))
    ax.legend()

    # Secondary axis: the injected link RTT behind each elevation.
    ax2 = ax.twiny()
    ax2.set_xlim(ax.get_xlim())
    ax2.set_xticks(x)
    ax2.set_xticklabels([f"{v:.1f}" for v in rtt], fontsize=8)
    ax2.set_xlabel("Injected link RTT (ms)", fontsize=9)
    ax2.grid(False)

    save(fig, f"fig_e2_{scenario}_latency.png")

    # ── Interruption ──────────────────────────────────────────────────────────
    gaps = [stats(grouped[e], "gap_ms")[0] for e in elevs]
    if any(g == g and g > 0 for g in gaps):
        fig, ax = plt.subplots()
        errs = [stats(grouped[e], "gap_ms")[1] for e in elevs]
        ax.errorbar(x, gaps, yerr=errs, marker="s", capsize=3,
                    color=COLORS["gap"], linewidth=1.8, markersize=5)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlabel("Satellite elevation angle (deg)   [overhead -> horizon]")
        ax.set_ylabel("User-plane interruption (ms)")
        ax.set_title(f"Interruption -- {SCENARIO_LABEL.get(scenario, scenario)}")
        save(fig, f"fig_e2_{scenario}_interruption.png")


def plot_comparison(data: dict[str, list[dict]]) -> None:
    """Total latency and interruption across architectures, versus link RTT."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.0, 4.2))

    for scenario, rows in data.items():
        grouped = group_by_elevation(rows)
        elevs = list(grouped.keys())
        rtt = [float(grouped[e][0]["rtt_ms"]) for e in elevs]
        total = [stats(grouped[e], "total_ms")[0] for e in elevs]
        gap = [stats(grouped[e], "gap_ms")[0] for e in elevs]

        order = np.argsort(rtt)
        rtt_s = np.asarray(rtt)[order]
        ax1.plot(rtt_s, np.asarray(total)[order], marker="o",
                 label=SCENARIO_LABEL.get(scenario, scenario), linewidth=1.8)
        ax2.plot(rtt_s, np.asarray(gap)[order], marker="s",
                 label=SCENARIO_LABEL.get(scenario, scenario), linewidth=1.8)

    ax1.set_xlabel("Injected link RTT (ms)")
    ax1.set_ylabel("Total handover latency (ms)")
    ax1.set_title("Handover latency")
    ax1.legend(fontsize=8)

    ax2.set_xlabel("Injected link RTT (ms)")
    ax2.set_ylabel("User-plane interruption (ms)")
    ax2.set_title("Interruption")
    ax2.legend(fontsize=8)

    fig.suptitle("Which link the delay sits on changes what it costs", y=1.02)
    save(fig, "fig_e2_comparison.png")


def plot_sensitivity(data: dict[str, list[dict]]) -> None:
    """
    Slope of total latency against injected link RTT, per architecture.

    This is the coefficient an optimization model needs: how many milliseconds
    of handover cost each millisecond of delay on a given link actually buys.
    """
    names, slopes = [], []
    for scenario, rows in data.items():
        grouped = group_by_elevation(rows)
        rtt = np.asarray([float(grouped[e][0]["rtt_ms"]) for e in grouped])
        total = np.asarray([stats(grouped[e], "total_ms")[0] for e in grouped])
        mask = ~np.isnan(total)
        if mask.sum() < 2:
            continue
        slope = float(np.polyfit(rtt[mask], total[mask], 1)[0])
        names.append(SCENARIO_LABEL.get(scenario, scenario))
        slopes.append(slope)

    if not names:
        return

    fig, ax = plt.subplots(figsize=(7.0, 3.6))
    y = np.arange(len(names))
    ax.barh(y, slopes, color=[COLORS["arrival"], COLORS["psw"], COLORS["alt"]][:len(names)])
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("Δ total handover latency per ms of link RTT (ms/ms)")
    ax.set_title("Sensitivity of handover latency to each link")
    for i, s in enumerate(slopes):
        ax.text(s, i, f" {s:.2f}", va="center", fontsize=9)
    save(fig, "fig_e2_sensitivity.png")


def main() -> None:
    apply_style()
    wanted = sys.argv[1:] or SCENARIOS

    data = {}
    for s in wanted:
        rows = ok_rows(load_csv(os.path.join(RAW_DIR, f"e2_{s}.csv")))
        if rows:
            data[s] = rows
            print(f"{s}: {len(rows)} successful handovers")
        else:
            print(f"{s}: no data (skipped)")

    if not data:
        sys.exit("Nothing to plot -- run run/e2_elevation_sweep.py first.")

    for s, rows in data.items():
        plot_scenario(s, rows)

    if len(data) > 1:
        plot_comparison(data)
        plot_sensitivity(data)


if __name__ == "__main__":
    main()
