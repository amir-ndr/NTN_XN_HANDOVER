#!/usr/bin/env python3
"""
Figures describing the NTN geometry the delays are derived from.

    python3 plots/plot_geometry.py

Produces:
    fig_geo_pass.png       elevation and one-way delay across a real pass
    fig_geo_elevation.png  delay vs elevation, with the sweep points marked
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_style import (COLORS, GEOM_DIR, apply_style, load_csv, save)  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402


def main() -> None:
    apply_style()

    profile = load_csv(os.path.join(GEOM_DIR, "pass_profile.csv"))
    points = load_csv(os.path.join(GEOM_DIR, "elevation_points.csv"))
    if not profile:
        sys.exit("No geometry data -- run lib/ntn_geometry.py first.")

    sat = profile[0]["satellite"]
    elev = np.array([float(r["elevation_deg"]) for r in profile])
    oneway = np.array([float(r["one_way_ms"]) for r in profile])
    rng = np.array([float(r["slant_range_km"]) for r in profile])
    t = np.arange(len(profile)) / (len(profile) - 1)

    # ── The pass over time ────────────────────────────────────────────────────
    fig, ax = plt.subplots()
    ax.plot(t, elev, color=COLORS["arrival"], linewidth=2, label="Elevation")
    ax.set_xlabel("Normalised time through the pass")
    ax.set_ylabel("Elevation angle (deg)", color=COLORS["arrival"])
    ax.tick_params(axis="y", labelcolor=COLORS["arrival"])

    ax2 = ax.twinx()
    ax2.plot(t, oneway, color=COLORS["psw"], linewidth=2,
             linestyle="--", label="One-way delay")
    ax2.set_ylabel("One-way propagation delay (ms)", color=COLORS["psw"])
    ax2.tick_params(axis="y", labelcolor=COLORS["psw"])
    ax2.grid(False)

    ax.set_title(f"{sat} over Montreal — a single pass")
    save(fig, "fig_geo_pass.png")

    # ── Delay vs elevation ────────────────────────────────────────────────────
    fig, ax = plt.subplots()
    order = np.argsort(elev)
    ax.plot(elev[order], oneway[order], color=COLORS["total"], linewidth=2,
            label="Ephemeris-derived")

    if points:
        pe = [float(r["actual_elevation_deg"]) for r in points]
        pd = [float(r["one_way_ms"]) for r in points]
        ax.scatter(pe, pd, color=COLORS["gap"], zorder=5, s=45,
                   label="Sweep points", edgecolor="white", linewidth=0.8)
        for e, d in zip(pe, pd):
            ax.annotate(f"{d:.2f}", (e, d), textcoords="offset points",
                        xytext=(6, 5), fontsize=7)

    ax.invert_xaxis()   # overhead on the left, horizon on the right
    ax.set_xlabel("Elevation angle (deg)   [overhead -> horizon]")
    ax.set_ylabel("One-way propagation delay (ms)")
    ax.set_title(f"Propagation delay vs elevation ({sat}, {rng.min():.0f}–{rng.max():.0f} km)")
    ax.legend()
    save(fig, "fig_geo_elevation.png")

    print(f"\n  satellite      : {sat}")
    print(f"  slant range    : {rng.min():.1f} – {rng.max():.1f} km")
    print(f"  one-way delay  : {oneway.min():.3f} – {oneway.max():.3f} ms")
    print(f"  RTT            : {2*oneway.min():.3f} – {2*oneway.max():.3f} ms")


if __name__ == "__main__":
    main()
