#!/usr/bin/env python3
"""
E3 -- Offline comparison of satellite-selection policies.

Replays the real constellation schedule and works out what each policy would
decide. No testbed involved: this is a pure decision-level study, and its output
is also the handover plan the live orchestrator will later execute.

    python3 run/e3_policy_compare.py --window 900

Outputs (results/summary/):
    policy_comparison.csv      one row per policy
    policy_<name>_handovers.csv  the decision timeline for each policy
    policy_<name>_serving.csv    which satellite is serving at each instant
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import ho_policy  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_HERE, "..", "results", "summary")

POLICIES = ["max_elevation", "elevation_threshold", "max_service_time"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=float, default=900.0,
                    help="seconds of the schedule to simulate (default 900 = 15 min)")
    ap.add_argument("--hysteresis", type=float, default=5.0,
                    help="degrees a candidate must beat the serving cell by")
    ap.add_argument("--ttt", type=float, default=5.0,
                    help="seconds the condition must hold before firing")
    ap.add_argument("--min-dwell", type=float, default=20.0,
                    help="minimum seconds between handovers")
    ap.add_argument("--lead-time", type=float, default=15.0,
                    help="fire this long before the serving satellite sets; "
                         "must exceed the measured handover latency (E2)")
    ap.add_argument("--elev-threshold", type=float, default=25.0,
                    help="elevation_threshold policy: switch below this")
    args = ap.parse_args()

    schedule = ho_policy.load_schedule(window_s=args.window)
    if not schedule:
        sys.exit("Empty schedule -- run lib/ntn_constellation.py first.")

    times = sorted(schedule)
    duration = times[-1] - times[0]
    n_sats = len({v.satellite for vs in schedule.values() for v in vs})
    peak = max(len(vs) for vs in schedule.values())

    print(f"Schedule: {duration:.0f} s ({duration/60:.1f} min), "
          f"{n_sats} satellites, up to {peak} visible at once")
    print(f"Controls: hysteresis {args.hysteresis} deg, TTT {args.ttt} s, "
          f"min dwell {args.min_dwell} s, lead time {args.lead_time} s\n")

    os.makedirs(OUT_DIR, exist_ok=True)
    summaries = []

    for name in POLICIES:
        cfg = ho_policy.PolicyConfig(
            name=name,
            hysteresis_deg=args.hysteresis,
            time_to_trigger_s=args.ttt,
            min_dwell_s=args.min_dwell,
            lead_time_s=args.lead_time,
            elevation_threshold_deg=args.elev_threshold,
        )
        res = ho_policy.simulate(cfg, schedule)
        s = ho_policy.summarise(res, duration)
        summaries.append(s)

        print(f"{name:<22} {s['handovers']:3d} handovers "
              f"({s['handovers_per_min']:5.2f}/min)  "
              f"late {s['late_handovers']:2d}  outage {s['outage_s']:5.1f}s  "
              f"mean elev {s['mean_serving_elev_deg']:5.1f} deg  "
              f"mean delay {s['mean_serving_one_way_ms']:5.2f} ms")

        with open(os.path.join(OUT_DIR, f"policy_{name}_handovers.csv"),
                  "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "from_sat", "to_sat", "from_elev_deg", "to_elev_deg",
                        "from_one_way_ms", "to_one_way_ms", "reason"])
            for h in res.handovers:
                w.writerow([h.t_s, h.from_sat, h.to_sat, round(h.from_elev, 3),
                            round(h.to_elev, 3), h.from_one_way_ms,
                            h.to_one_way_ms, h.reason])

        with open(os.path.join(OUT_DIR, f"policy_{name}_serving.csv"),
                  "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["t_s", "satellite",
                                              "elevation_deg", "one_way_ms"])
            w.writeheader()
            w.writerows(res.serving_trace)

    comp = os.path.join(OUT_DIR, "policy_comparison.csv")
    with open(comp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summaries[0]))
        w.writeheader()
        w.writerows(summaries)

    print(f"\nWrote {comp} and per-policy timelines in {os.path.relpath(OUT_DIR)}")


if __name__ == "__main__":
    main()
