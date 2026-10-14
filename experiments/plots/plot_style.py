#!/usr/bin/env python3
"""Shared plot styling so every figure in the manuscript looks consistent."""

from __future__ import annotations

import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(_HERE, "..", "results", "raw")
GEOM_DIR = os.path.join(_HERE, "..", "results", "geometry")
SUMMARY_DIR = os.path.join(_HERE, "..", "results", "summary")
FIG_DIR = os.path.join(_HERE, "..", "figures")

# Colour-blind safe, prints legibly in greyscale.
COLORS = {
    "arrival": "#0072B2",
    "psw": "#E69F00",
    "total": "#009E73",
    "gap": "#D55E00",
    "alt": "#CC79A7",
}

SCENARIO_LABEL = {
    "transparent": "Transparent payload (radio link)",
    "regenerative": "Regenerative payload (feeder link, N2+N3)",
    "isl": "Inter-satellite link (Xn)",
}


def apply_style() -> None:
    plt.rcParams.update({
        "figure.figsize": (7.0, 4.2),
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "grid.linestyle": "--",
        "legend.frameon": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def ok_rows(rows: list[dict]) -> list[dict]:
    """Keep only successful handovers -- failures have no timing to plot."""
    return [r for r in rows if r.get("ok") == "1"]


def fnum(rows: list[dict], key: str) -> list[float]:
    out = []
    for r in rows:
        try:
            v = float(r[key])
        except (KeyError, ValueError, TypeError):
            continue
        if v == v:  # drop NaN
            out.append(v)
    return out


def save(fig, name: str) -> str:
    os.makedirs(FIG_DIR, exist_ok=True)
    path = os.path.join(FIG_DIR, name)
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {os.path.relpath(path)}")
    return path
