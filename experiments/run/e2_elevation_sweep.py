#!/usr/bin/env python3
"""
E2 -- Handover performance versus satellite elevation angle.

Sweeps the elevation points derived from real Starlink ephemeris over Montreal
(lib/ntn_geometry.py) and, at each point, injects the corresponding one-way
propagation delay on the link that the chosen NTN architecture makes long:

    transparent   satellite is a bent-pipe relay, gNB on the ground
                  -> delay on the RADIO link (service + feeder hop)
    regenerative  gNB on board the satellite
                  -> delay on the FEEDER link, i.e. N2 + N3 to the core
    isl           source and target gNB on different satellites
                  -> delay on the XN link between them

Each architecture loads a different part of the handover, so the same
propagation delay costs a different amount of interruption depending on where
it sits. That difference is the point of the experiment.

    sudo python3 run/e2_elevation_sweep.py --scenario regenerative --runs 20

Output: results/raw/e2_<scenario>.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import testbed  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
GEOM_CSV = os.path.join(_HERE, "..", "results", "geometry", "elevation_points.csv")
OUT_DIR = os.path.join(_HERE, "..", "results", "raw")

SCENARIOS = {
    "transparent": "radio",
    "regenerative": "feeder",
    "isl": "xn",
}

FIELDS = [
    "experiment", "scenario", "link", "elevation_deg", "slant_range_km",
    "one_way_ms", "rtt_ms", "run_idx", "source_gnb", "target_gnb",
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


def load_elevation_points() -> list[dict]:
    if not os.path.exists(GEOM_CSV):
        sys.exit(f"Missing {GEOM_CSV}\nRun:  python3 lib/ntn_geometry.py")
    with open(GEOM_CSV) as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="regenerative")
    ap.add_argument("--runs", type=int, default=20,
                    help="handovers per elevation point")
    ap.add_argument("--period", type=float, default=6.0,
                    help="fixed seconds per handover cycle, held constant across "
                         "all elevations so cadence does not confound the sweep")
    ap.add_argument("--link-settle", type=float, default=3.0,
                    help="extra wait after changing the injected delay")
    ap.add_argument("--ping-interval", type=float, default=0.002,
                    help="ping spacing in seconds; sets the interruption resolution")
    ap.add_argument("--no-ping", action="store_true")
    ap.add_argument("--tag", default="",
                    help="suffix for the output file, so variant runs do not "
                         "overwrite each other (e.g. --tag noping)")
    args = ap.parse_args()

    link = SCENARIOS[args.scenario]
    points = load_elevation_points()

    problems = testbed.preflight()
    if problems:
        print("Testbed not ready:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    testbed.ensure_csv_header()
    os.makedirs(OUT_DIR, exist_ok=True)
    # A variant run must not clobber the baseline run of the same scenario.
    tag = args.tag or ("noping" if args.no_ping else "")
    suffix = f"_{tag}" if tag else ""
    out_csv = os.path.join(OUT_DIR, f"e2_{args.scenario}{suffix}.csv")

    print(f"E2 elevation sweep")
    print(f"  scenario : {args.scenario}  (delay on '{link}')")
    print(f"  points   : {len(points)} elevations x {args.runs} handovers")
    print()

    rows = []
    try:
        for pt in points:
            elev = float(pt["actual_elevation_deg"])
            one_way = float(pt["netem_delay_ms_per_side"])
            rtt = float(pt["rtt_ms"])

            testbed.set_delay(link, one_way)
            time.sleep(args.link_settle)

            print(f"elevation {elev:5.1f} deg | range {float(pt['slant_range_km']):7.1f} km "
                  f"| {one_way:.3f} ms one-way ({rtt:.3f} ms RTT) on {link}")

            for i in range(1, args.runs + 1):
                cycle_start = time.time()
                r = testbed.do_handover(measure_interruption=not args.no_ping,
                                 ping_interval_s=args.ping_interval)

                rows.append({
                    "experiment": "e2_elevation",
                    "scenario": args.scenario,
                    "link": link,
                    "elevation_deg": round(elev, 3),
                    "slant_range_km": round(float(pt["slant_range_km"]), 3),
                    "one_way_ms": one_way,
                    "rtt_ms": rtt,
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
                    print(f"    [{i:3d}/{args.runs}] arrival={r.ue_arrival_ms:6.2f} "
                          f"psw={r.psw_ms:6.2f} total={r.total_ms:6.2f} ms   "
                          f"gap={r.gap_packets:3d} pkt ({r.gap_ms:6.0f} ms)")
                else:
                    print(f"    [{i:3d}/{args.runs}] FAILED: {r.error}")

                _pace(cycle_start, args.period)
            print()
    finally:
        # Always leave the testbed undelayed, even on Ctrl-C.
        testbed.clear_delay()

        if rows:
            with open(out_csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            ok = sum(r["ok"] for r in rows)
            print(f"{ok}/{len(rows)} handovers succeeded")
            print(f"Wrote {out_csv}")


if __name__ == "__main__":
    main()
