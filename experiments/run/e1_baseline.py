#!/usr/bin/env python3
"""
E1 -- Baseline handover characterisation (no injected delay).

Repeats Xn handovers back and forth between gnb1 and gnb2 with a ping running
across each switch, producing the reference distribution that every delayed
configuration in E2 is compared against.

    sudo python3 run/e1_baseline.py --runs 50

Output: results/raw/e1_baseline.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import testbed  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "raw")
OUT_CSV = os.path.join(OUT_DIR, "e1_baseline.csv")

FIELDS = [
    "experiment", "run_idx", "source_gnb", "target_gnb",
    "sst", "ue_arrival_ms", "psw_ms", "total_ms",
    "ping_sent", "ping_recv", "ping_lost", "gap_packets", "gap_ms",
    "ping_interval_actual_ms", "ping_rtt_avg_ms", "ok", "error", "timestamp",
]


def _pace(cycle_start: float, period: float) -> None:
    """Sleep so every handover cycle takes the same wall-clock time."""
    remaining = period - (time.time() - cycle_start)
    if remaining > 0:
        time.sleep(remaining)
    else:
        print(f"      (cycle overran the {period:.1f}s period by "
              f"{-remaining:.2f}s -- raise --period)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=50, help="number of handovers")
    ap.add_argument("--period", type=float, default=6.0,
                    help="fixed seconds per handover cycle. Held constant so the "
                         "core sees the same handover rate in every configuration; "
                         "cadence strongly affects psw_ms and would otherwise "
                         "confound the sweep")
    ap.add_argument("--ping-interval", type=float, default=0.002,
                    help="ping spacing in seconds; sets the interruption resolution")
    ap.add_argument("--no-ping", action="store_true",
                    help="skip the interruption measurement")
    ap.add_argument("--tag", default="",
                    help="suffix for the output file, so a repeat baseline does "
                         "not overwrite the first (e.g. --tag after)")
    args = ap.parse_args()

    problems = testbed.preflight()
    if problems:
        print("Testbed not ready:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    # Start from a clean link state so the baseline really is undelayed.
    testbed.clear_delay()
    testbed.ensure_csv_header()

    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []

    print(f"E1 baseline: {args.runs} handovers, no injected delay\n")
    ok_count = 0

    for i in range(1, args.runs + 1):
        cycle_start = time.time()
        r = testbed.do_handover(measure_interruption=not args.no_ping,
                                 ping_interval_s=args.ping_interval)

        rows.append({
            "experiment": "e1_baseline",
            "run_idx": i,
            "source_gnb": r.source_gnb,
            "target_gnb": r.target_gnb,
            "sst": r.sst,
            "ue_arrival_ms": r.ue_arrival_ms,
            "psw_ms": r.psw_ms,
            "total_ms": r.total_ms,
            "ping_sent": r.ping_sent,
            "ping_recv": r.ping_recv,
            "ping_lost": r.ping_lost,
            "gap_packets": r.gap_packets,
            "gap_ms": round(r.gap_ms, 3),
            "ping_rtt_avg_ms": r.ping_rtt_avg_ms,
                    "ping_interval_actual_ms": round(r.ping_interval_actual_ms, 4)
                        if r.ping_interval_actual_ms == r.ping_interval_actual_ms else "",
            "ok": int(r.ok),
            "error": r.error,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

        if r.ok:
            ok_count += 1
            print(f"  [{i:3d}/{args.runs}] gnb{r.source_gnb}->gnb{r.target_gnb}  "
                  f"arrival={r.ue_arrival_ms:6.2f}  psw={r.psw_ms:6.2f}  "
                  f"total={r.total_ms:6.2f} ms   gap={r.gap_packets} pkt "
                  f"({r.gap_ms:.0f} ms)")
        else:
            print(f"  [{i:3d}/{args.runs}] FAILED: {r.error}")

        _pace(cycle_start, args.period)

    out_csv = OUT_CSV
    if args.tag:
        out_csv = OUT_CSV.replace(".csv", f"_{args.tag}.csv")

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"\n{ok_count}/{args.runs} succeeded")
    print(f"Wrote {out_csv}")


if __name__ == "__main__":
    main()
