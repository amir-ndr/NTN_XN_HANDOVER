#!/usr/bin/env python3
"""
E4 -- Live elevation-triggered handover over a real satellite pass sequence.

Replays the real Starlink geometry over Montreal against the running testbed:
keeps each gNB's service-link delay matched to whichever satellite it currently
represents, and fires a real Xn handover when the policy says to. Every handover
is a genuine 5G procedure, measured the same way as E1/E2.

    sudo python3 run/e4_orchestrator.py --policy max_service_time --speed 10

SATELLITE -> gNB MAPPING (role recycling)
    Only two gNBs are needed for an arbitrarily long chain. The serving gNB
    represents the current satellite; the other represents the next one the
    policy has chosen. After a handover the roles swap, and the freed gNB is
    reassigned to whichever satellite comes next:

        sat A -> gnb1                    gnb2 idle
          handover A->B
        sat B -> gnb2                    gnb1 free -> becomes sat C
          handover B->C
        sat C -> gnb1                    gnb2 free -> becomes sat D

TIME
    Geometry is replayed on an accelerated clock (--speed), because a LEO pass
    lasts minutes while a handover takes milliseconds. The handovers themselves
    are real time; only the geometry clock is compressed.

NAMING
    This is elevation-TRIGGERED handover, not CHO. The network decides and
    commands; in 3GPP CHO the UE holds candidate configurations and evaluates
    the conditions itself. See lib/ho_policy.py for how the trigger maps onto
    the standardised CondEvent D1/T1 conditions.

Output: results/raw/e4_<policy>.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import ho_policy  # noqa: E402
import testbed  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_HERE, "..", "results", "raw")

FIELDS = [
    "experiment", "policy", "window_offset_s", "ho_idx", "sim_time_s",
    "from_sat", "to_sat", "from_elev_deg", "to_elev_deg",
    "from_one_way_ms", "to_one_way_ms", "reason",
    "source_gnb", "target_gnb",
    "ue_arrival_ms", "psw_ms", "total_ms",
    "ping_sent", "ping_recv", "ping_lost", "gap_packets", "gap_ms",
    "ping_interval_actual_ms", "ping_rtt_avg_ms", "ok", "error", "wall_time",
]


def apply_delay(gnb: int, one_way_ms: float) -> None:
    """Point one gNB's service link at the satellite it currently represents."""
    testbed.set_delay(f"gnb{gnb}", one_way_ms)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", choices=["max_elevation", "elevation_threshold",
                                         "max_service_time"],
                    default="max_service_time")
    ap.add_argument("--window", type=float, default=900.0,
                    help="seconds of the schedule to replay")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="start this many seconds into the schedule. Different "
                         "offsets give independent stretches of the constellation, "
                         "so repeats are separate samples rather than reruns of "
                         "the same geometry")
    ap.add_argument("--tag", default="",
                    help="suffix for the output file so repeats do not overwrite "
                         "(e.g. --tag w900)")
    ap.add_argument("--speed", type=float, default=10.0,
                    help="geometry clock acceleration (10 = 10x real time)")
    ap.add_argument("--ping-interval", type=float, default=0.001)
    ap.add_argument("--no-ping", action="store_true")
    ap.add_argument("--hysteresis", type=float, default=5.0)
    ap.add_argument("--ttt", type=float, default=5.0)
    ap.add_argument("--min-dwell", type=float, default=20.0)
    ap.add_argument("--lead-time", type=float, default=15.0,
                    help="fire this long before the serving satellite sets; "
                         "must exceed the measured handover latency (E2)")
    ap.add_argument("--elev-threshold", type=float, default=25.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan without touching the testbed")
    args = ap.parse_args()

    # ── Plan: decide the handovers offline first ──────────────────────────────
    schedule = ho_policy.load_schedule(window_s=args.window,
                                       offset_s=args.offset)
    if not schedule:
        sys.exit("Empty schedule -- run lib/ntn_constellation.py first.")

    cfg = ho_policy.PolicyConfig(
        name=args.policy, hysteresis_deg=args.hysteresis,
        time_to_trigger_s=args.ttt, min_dwell_s=args.min_dwell,
        lead_time_s=args.lead_time,
        elevation_threshold_deg=args.elev_threshold)

    plan = ho_policy.simulate(cfg, schedule)
    times = sorted(schedule)
    t0 = times[0]

    print(f"E4 orchestrator")
    print(f"  policy   : {args.policy}")
    print(f"  window   : {args.offset:.0f}-{args.offset + args.window:.0f} s of "
          f"geometry at {args.speed:.0f}x -> {args.window/args.speed:.0f} s wall clock")
    print(f"  planned  : {len(plan.handovers)} handovers")
    for h in plan.handovers:
        print(f"     t={h.t_s - t0:6.0f}s  {h.from_sat} -> {h.to_sat}  "
              f"({h.from_elev:.1f} deg -> {h.to_elev:.1f} deg)  [{h.reason}]")

    if args.dry_run:
        print("\n(dry run -- testbed untouched)")
        return

    problems = testbed.preflight()
    if problems:
        print("\nTestbed not ready:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    serving_gnb, _ = testbed.find_serving_gnb()
    other_gnb = 2 if serving_gnb == 1 else 1
    print(f"\n  UE starts on gnb{serving_gnb}; gnb{other_gnb} holds the candidate\n")

    testbed.ensure_csv_header()
    os.makedirs(OUT_DIR, exist_ok=True)
    suffix = f"_{args.tag}" if args.tag else ""
    out_csv = os.path.join(OUT_DIR, f"e4_{args.policy}{suffix}.csv")

    rows = []
    pending = list(plan.handovers)
    serving_trace = {s["t_s"]: s for s in plan.serving_trace}
    wall_start = time.time()

    try:
        for t in times:
            sim_elapsed = t - t0
            # Wait until the accelerated clock reaches this sample.
            target_wall = wall_start + sim_elapsed / args.speed
            slack = target_wall - time.time()
            if slack > 0:
                time.sleep(slack)

            # Serving gNB carries the current satellite's service-link delay.
            cur = serving_trace.get(t)
            if cur and cur["satellite"]:
                apply_delay(serving_gnb, cur["one_way_ms"])

            # The candidate gNB carries the delay of the satellite we will move
            # to next, so the target link is already correct when we hand over.
            if pending:
                nxt = pending[0]
                cand = next((v for v in schedule[t] if v.satellite == nxt.to_sat), None)
                if cand:
                    apply_delay(other_gnb, cand.one_way_ms)

            # Due for a handover?
            if pending and t >= pending[0].t_s:
                h = pending.pop(0)
                idx = len(rows) + 1
                print(f"  [{idx}] t={h.t_s - t0:6.0f}s  {h.from_sat} -> {h.to_sat}   "
                      f"gnb{serving_gnb} -> gnb{other_gnb}   "
                      f"({h.from_elev:.1f} -> {h.to_elev:.1f} deg)")

                r = testbed.do_handover(target_gnb=other_gnb,
                                        measure_interruption=not args.no_ping,
                                        ping_interval_s=args.ping_interval)

                rows.append({
                    "experiment": "e4_orchestrator", "policy": args.policy,
                    "window_offset_s": args.offset,
                    "ho_idx": idx, "sim_time_s": round(h.t_s - t0, 1),
                    "from_sat": h.from_sat, "to_sat": h.to_sat,
                    "from_elev_deg": round(h.from_elev, 3),
                    "to_elev_deg": round(h.to_elev, 3),
                    "from_one_way_ms": h.from_one_way_ms,
                    "to_one_way_ms": h.to_one_way_ms,
                    "reason": h.reason,
                    "source_gnb": r.source_gnb, "target_gnb": r.target_gnb,
                    "ue_arrival_ms": r.ue_arrival_ms, "psw_ms": r.psw_ms,
                    "total_ms": r.total_ms,
                    "ping_sent": r.ping_sent, "ping_recv": r.ping_recv,
                    "ping_lost": r.ping_lost, "gap_packets": r.gap_packets,
                    "gap_ms": round(r.gap_ms, 3),
                    "ping_rtt_avg_ms": r.ping_rtt_avg_ms,
                    "ping_interval_actual_ms": round(r.ping_interval_actual_ms, 4)
                        if r.ping_interval_actual_ms == r.ping_interval_actual_ms else "",
                    "ok": int(r.ok), "error": r.error,
                    "wall_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

                if r.ok:
                    print(f"        arrival={r.ue_arrival_ms:.2f} psw={r.psw_ms:.2f} "
                          f"total={r.total_ms:.2f} ms   gap={r.gap_packets} pkt "
                          f"({r.gap_ms:.0f} ms)")
                    # Roles swap; the freed gNB becomes the next candidate.
                    serving_gnb, other_gnb = other_gnb, serving_gnb
                else:
                    print(f"        FAILED: {r.error}")
                    # Resynchronise rather than assuming the UE moved.
                    s, _ = testbed.find_serving_gnb()
                    if s:
                        serving_gnb = s
                        other_gnb = 2 if s == 1 else 1
    finally:
        testbed.clear_delay()

        if rows:
            with open(out_csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            ok = sum(r["ok"] for r in rows)
            print(f"\n{ok}/{len(rows)} handovers succeeded")
            print(f"Wrote {out_csv}")
        else:
            print("\nNo handovers executed.")


if __name__ == "__main__":
    main()
