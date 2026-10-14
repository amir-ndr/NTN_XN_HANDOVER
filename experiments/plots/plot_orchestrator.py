#!/usr/bin/env python3
"""
Figures for E4 -- live elevation-triggered handover over real Starlink geometry.

E4 is the experiment that joins the two halves together: the geometry decides
WHEN to hand over and WHAT delay each link carries, and the testbed reports what
each of those handovers actually COST. E1/E2 measure cost under controlled
delay; E3 decides handovers offline; only E4 has both at once.

    python3 plots/plot_orchestrator.py

Produces:
    fig_e4_timeline.png     serving elevation through the pass, handovers marked
                            with their measured cost
    fig_e4_cost_vs_elev.png measured handover cost against the elevation it fired at
    fig_e4_frontier.png     link quality vs cumulative interruption, per policy
                            -- the trade-off an optimizer acts on
    fig_e4_summary.png      per-policy totals
"""

from __future__ import annotations

import os
import sys

import glob

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_style import (COLORS, RAW_DIR, SUMMARY_DIR, apply_style,  # noqa: E402
                        load_csv, ok_rows, save)

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


def load_all() -> dict[str, list[dict]]:
    """
    Gather every window of every policy: e4_<policy>.csv and e4_<policy>_<tag>.csv.

    Each window is an independent stretch of the constellation, so repeats are
    separate samples rather than reruns of the same geometry -- which is what
    makes an error bar meaningful.
    """
    out = {}
    for p in POLICIES:
        rows = []
        for path in sorted(glob.glob(os.path.join(RAW_DIR, f"e4_{p}*.csv"))):
            rows.extend(ok_rows(load_csv(path)))
        if rows:
            out[p] = rows
    return out


def by_window(rows) -> dict[str, list[dict]]:
    """Split a policy's handovers back into the windows they came from."""
    g = {}
    for r in rows:
        g.setdefault(r.get("window_offset_s", "0"), []).append(r)
    return g


def f(rows, key):
    return np.array([float(r[key]) for r in rows])


def outage_ms(rows) -> np.ndarray:
    """
    Outage recomputed from primitives: (contiguous packets missed) x (spacing
    actually achieved).

    Computed here rather than read from gap_ms because CSVs written before the
    metric was corrected hold a max-inter-arrival value, which picks up the
    worst jitter spike rather than the handover. Recomputing from gap_packets
    and ping_interval_actual_ms gives the same answer for new runs and repairs
    older ones.
    """
    out = []
    for r in rows:
        ai = r.get("ping_interval_actual_ms", "")
        try:
            ai = float(ai)
        except (TypeError, ValueError):
            ai = float("nan")
        out.append(int(r["gap_packets"]) * ai)
    return np.array(out)


def main() -> None:
    apply_style()
    data = load_all()
    if not data:
        sys.exit("No E4 data -- run run/e4_orchestrator.py first.")

    # ── Timeline: geometry + the handovers it caused, sized by cost ───────────
    fig, axes = plt.subplots(len(data), 1, figsize=(8.4, 7.2), sharex=True)
    if len(data) == 1:
        axes = [axes]

    for ax, (pol, rows) in zip(axes, data.items()):
        serving = load_csv(os.path.join(SUMMARY_DIR, f"policy_{pol}_serving.csv"))
        if serving:
            t = f(serving, "t_s")
            e = np.array([float(r["elevation_deg"]) if r["elevation_deg"] else np.nan
                          for r in serving])
            ax.plot(t - t[0], e, color=PCOLOR[pol], linewidth=1.7, zorder=2)

        ho_t = f(rows, "sim_time_s")
        ho_e = f(rows, "to_elev_deg")
        cost = f(rows, "total_ms")

        # Marker area scales with measured handover cost.
        ax.scatter(ho_t, ho_e, s=cost * 12, color=COLORS["gap"], zorder=5,
                   edgecolor="white", linewidth=1.0,
                   label="handover (area = measured cost)")
        for x, y, c in zip(ho_t, ho_e, cost):
            ax.annotate(f"{c:.0f} ms", (x, y), textcoords="offset points",
                        xytext=(0, 11), ha="center", fontsize=7)

        ax.set_ylabel("Serving\nelevation (deg)")
        ax.set_ylim(0, 95)
        ax.set_title(f"{LABEL[pol]} — {len(rows)} handovers, "
                     f"{cost.sum():.0f} ms total handover time",
                     loc="left", fontsize=10)

    axes[0].legend(fontsize=8, loc="upper right")
    axes[-1].set_xlabel("Time through the pass sequence (s)")
    fig.suptitle("Real Starlink geometry driving real handovers (Montreal)",
                 y=0.995, fontsize=11)
    save(fig, "fig_e4_timeline.png")

    # ── Cost vs the elevation it fired at ─────────────────────────────────────
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    for pol, rows in data.items():
        # Elevation of the link being left; the source link carries the command.
        elev = np.array([float(r["from_elev_deg"]) if r["from_elev_deg"] != "nan"
                         else np.nan for r in rows])
        ax.scatter(elev, f(rows, "total_ms"), s=70, color=PCOLOR[pol],
                   edgecolor="white", linewidth=0.9, label=LABEL[pol], zorder=5)

    allrows = [r for rows in data.values() for r in rows]
    ev = np.array([float(r["from_elev_deg"]) if r["from_elev_deg"] != "nan"
                   else np.nan for r in allrows])
    cs = f(allrows, "total_ms")
    m = ~np.isnan(ev)
    if m.sum() >= 2:
        k = np.polyfit(ev[m], cs[m], 1)
        xs = np.linspace(np.nanmin(ev), np.nanmax(ev), 50)
        ax.plot(xs, np.polyval(k, xs), "--", color="#666", linewidth=1.2,
                label=f"fit: {k[0]:+.2f} ms per deg")

    ax.invert_xaxis()
    ax.set_xlabel("Elevation of the satellite being left (deg)   [overhead -> horizon]")
    ax.set_ylabel("Measured handover cost (ms)")
    ax.set_title("Handing over late costs more: longer links, slower procedure")
    ax.legend(fontsize=8)
    save(fig, "fig_e4_cost_vs_elev.png")

    # ── The frontier an optimizer actually trades on ──────────────────────────
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    for pol, rows in data.items():
        wins = by_window(rows)
        outs, links = [], []
        for w in wins.values():
            g = outage_ms(w); g = g[~np.isnan(g)]
            outs.append(g.sum())
            served = np.concatenate([f(w, "from_one_way_ms"), f(w, "to_one_way_ms")])
            links.append(np.nanmean(served))
        outs, links = np.array(outs), np.array(links)

        ax.errorbar(outs.mean(), links.mean(),
                    xerr=outs.std() if len(outs) > 1 else None,
                    yerr=links.std() if len(links) > 1 else None,
                    fmt="o", markersize=13, capsize=4, color=PCOLOR[pol],
                    markeredgecolor="white", markeredgewidth=1.2,
                    label=f"{LABEL[pol]} — {len(rows)/max(1,len(wins)):.1f} HO/window "
                          f"(n={len(wins)})", zorder=5)

    ax.set_xlabel("Cumulative user-plane interruption per 15-min window (ms)")
    ax.set_ylabel("Mean served one-way link delay (ms)")
    ax.set_title("The trade-off: fewer, costlier handovers vs a worse average link")
    ax.legend(fontsize=8, loc="upper left")
    ax.margins(0.18)
    ax.invert_yaxis()   # up = better link
    save(fig, "fig_e4_frontier.png")

    # ── Summary bars ──────────────────────────────────────────────────────────
    metrics = [("handovers", "Handovers"),
               ("cum_cost", "Total handover\ntime (ms)"),
               ("cum_out", "Cumulative\ninterruption (ms)"),
               ("mean_delay", "Mean served\nlink delay (ms)")]
    vals = {k: [] for k, _ in metrics}
    names = list(data)
    for pol in names:
        rows = data[pol]
        gaps = outage_ms(rows); gaps = gaps[~np.isnan(gaps)]
        served = np.concatenate([f(rows, "from_one_way_ms"), f(rows, "to_one_way_ms")])
        served = served[~np.isnan(served)]
        vals["handovers"].append(len(rows))
        vals["cum_cost"].append(f(rows, "total_ms").sum())
        vals["cum_out"].append(gaps.sum())
        vals["mean_delay"].append(served.mean())

    fig, axs = plt.subplots(1, len(metrics), figsize=(12.5, 3.5))
    x = np.arange(len(names))
    for ax, (key, title) in zip(axs, metrics):
        ax.bar(x, vals[key], color=[PCOLOR[n] for n in names])
        ax.set_xticks(x)
        ax.set_xticklabels([LABEL[n].replace(" ", "\n") for n in names], fontsize=7)
        ax.set_title(title, fontsize=9)
        for i, v in enumerate(vals[key]):
            ax.text(i, v, f"{v:.1f}" if v % 1 else f"{v:.0f}",
                    ha="center", va="bottom", fontsize=8)
        ax.set_ylim(0, max(vals[key]) * 1.25)
    fig.suptitle("E4: measured cost of each policy over a real pass sequence",
                 y=1.05, fontsize=10)
    save(fig, "fig_e4_summary.png")

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n{'policy':<26}{'HO':>4}{'tot cost':>10}{'cum outage':>12}"
          f"{'mean link':>11}{'cost/HO':>9}")
    for i, pol in enumerate(names):
        print(f"{LABEL[pol]:<26}{vals['handovers'][i]:>4}"
              f"{vals['cum_cost'][i]:>9.1f}ms{vals['cum_out'][i]:>10.1f}ms"
              f"{vals['mean_delay'][i]:>9.2f}ms"
              f"{vals['cum_cost'][i]/vals['handovers'][i]:>8.1f}ms")


if __name__ == "__main__":
    main()
