#!/usr/bin/env python3
"""
Print the paper's numbers straight from the raw CSVs in results/raw/.

    python3 plots/paper_tables.py

Sections:
    E1  baseline (Section VI-A): medians, mean +- SD, 95th percentiles,
        interruption, and the drift check against the post-session baseline
    E2  Table III: ms of each metric per ms of injected link RTT
    E3  offline policy comparison (Section VI-C)
    E4  Table IV: measured policy cost over the four 15-minute windows,
        plus the repeat-window check (Section VI-D)

Definitions (the same ones the paper uses):
    interruption  contiguous missing probes x probe spacing actually achieved
                  (gap_packets x ping_interval_actual_ms)
    SD            population standard deviation
    95th pct      numpy "lower" method (an observed value, no interpolation)
    Table III     ordinary least-squares slope over every successful handover
                  of a scenario (140 each), metric vs injected link RTT
    Table IV      HO per window   = handovers / window, averaged over windows
                  cost per HO     = total_ms over all handovers, mean +- SD
                  cum. outage     = per-window sum of interruption, mean +- SD
                  mean link delay = mean one-way delay of the target satellite
                                    at handover (to_one_way_ms)
"""

from __future__ import annotations

import csv
import glob
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(_HERE, "..", "results", "raw")
SUMMARY = os.path.join(_HERE, "..", "results", "summary")

POLICIES = [("max_elevation", "Max. elevation"),
            ("elevation_threshold", "Elev. threshold"),
            ("max_service_time", "Max. service time")]
SCENARIOS = [("regenerative", "Regenerative"),
             ("transparent", "Transparent"),
             ("isl", "Inter-satellite")]
WINDOWS = ["w0", "w900", "w1800", "w2700"]


def ok_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("ok") == "1"]


def col(rows: list[dict], key: str) -> np.ndarray:
    return np.array([float(r[key]) for r in rows])


def outage(rows: list[dict]) -> np.ndarray:
    return np.array([int(r["gap_packets"]) * float(r["ping_interval_actual_ms"])
                     for r in rows])


def e1() -> None:
    rows = ok_rows(os.path.join(RAW, "e1_baseline.csv"))
    if not rows:
        print("E1: no data (results/raw/e1_baseline.csv)\n")
        return
    print(f"E1 baseline, no injected delay: n = {len(rows)} handovers")
    for key, name in (("ue_arrival_ms", "UE arrival"), ("psw_ms", "Path switch"),
                      ("total_ms", "Total")):
        v = col(rows, key)
        print(f"  {name:12s} median {np.median(v):6.3f}   mean {v.mean():6.3f} +- {v.std():.3f}"
              f"   p95 {np.percentile(v, 95, method='lower'):6.3f} ms")
    print(f"  {'Interruption':12s} median {np.median(outage(rows)):6.3f} ms")
    after = ok_rows(os.path.join(RAW, "e1_baseline_after.csv"))
    if after:
        print(f"  Drift check: path-switch median {np.median(col(rows, 'psw_ms')):.3f} ms before,"
              f" {np.median(col(after, 'psw_ms')):.3f} ms after the session (n = {len(after)})")
    print()


def table_iii() -> None:
    print("Table III: sensitivity to injected link RTT (ms per ms)")
    print(f"  {'Scenario':16s} {'n':>4s} {'UE arrival':>11s} {'Path switch':>12s}"
          f" {'Total':>7s} {'Interruption':>13s}")
    for scen, name in SCENARIOS:
        rows = ok_rows(os.path.join(RAW, f"e2_{scen}.csv"))
        if not rows:
            print(f"  {name:16s} no data")
            continue
        x = col(rows, "rtt_ms")
        slope = lambda y: np.polyfit(x, y, 1)[0]
        print(f"  {name:16s} {len(rows):4d} {slope(col(rows, 'ue_arrival_ms')):+11.2f}"
              f" {slope(col(rows, 'psw_ms')):+12.2f} {slope(col(rows, 'total_ms')):+7.2f}"
              f" {slope(outage(rows)):+13.2f}")
    print()


def e3() -> None:
    path = os.path.join(SUMMARY, "policy_comparison.csv")
    if not os.path.exists(path):
        print("E3: no data (run run/e3_policy_compare.py)\n")
        return
    print("E3: offline policy comparison over the real constellation schedule")
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            print("  " + "  ".join(f"{k}={v}" for k, v in r.items()))
    print()


def table_iv() -> None:
    print("Table IV: measured policy cost, mean +- SD over the windows")
    print(f"  {'Policy':18s} {'windows':>7s} {'HO/window':>9s} {'cost per HO (ms)':>17s}"
          f" {'cum. outage (ms)':>17s} {'mean link (ms)':>14s}")
    for pol, name in POLICIES:
        per_win = [ok_rows(os.path.join(RAW, f"e4_{pol}_{w}.csv")) for w in WINDOWS]
        per_win = [w for w in per_win if w]
        if not per_win:
            print(f"  {name:18s} no data")
            continue
        allr = [r for w in per_win for r in w]
        cost = col(allr, "total_ms")
        cum = np.array([outage(w).sum() for w in per_win])
        print(f"  {name:18s} {len(per_win):7d} {np.mean([len(w) for w in per_win]):9.1f}"
              f" {cost.mean():9.2f} +- {cost.std():4.2f} {cum.mean():9.1f} +- {cum.std():4.1f}"
              f" {col(allr, 'to_one_way_ms').mean():14.2f}")
    print("  Repeat of window 0 (cumulative handover cost, ms):")
    for pol, name in POLICIES:
        rep = ok_rows(os.path.join(RAW, "repeatability", f"e4_{pol}.csv"))
        w0 = ok_rows(os.path.join(RAW, f"e4_{pol}_w0.csv"))
        if rep and w0:
            print(f"    {name:18s} {col(w0, 'total_ms').sum():6.1f} vs {col(rep, 'total_ms').sum():6.1f}")
    print()


if __name__ == "__main__":
    e1()
    table_iii()
    e3()
    table_iv()
