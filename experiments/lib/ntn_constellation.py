#!/usr/bin/env python3
"""
Constellation visibility schedule: which satellites are usable, when, with what
propagation delay, and how much service time each has left.

lib/ntn_geometry.py characterises ONE pass, which is what the E2 elevation sweep
needs. A sequential handover chain needs more: a time series of every visible
satellite, so a serving satellite can be handed off to a rising one before it
sets. This module produces that.

The handover POLICY lives in the orchestrator, not here. This module only states
what is geometrically visible; deciding when and where to switch is separate.

ON TRIGGER SEMANTICS
    3GPP does not define an elevation-angle event. The NTN-specific Rel-17
    conditional-handover triggers are CondEvent D1 (location/distance based:
    distance to serving reference location above one threshold AND distance to
    candidate below another) and CondEvent T1 (time based).

    Elevation is used as a trigger throughout the LEO literature, and because
    elevation and slant range are monotonically related (higher elevation ->
    shorter range), an elevation threshold is equivalent to a D1-style distance
    condition. Both elevation and slant range are emitted here so a policy can
    be expressed either way.

    'remaining_service_s' supports the max-remaining-service-time policy, which
    the literature finds superior to a fixed elevation threshold: two passes can
    share an elevation while having very different time left before outage.

Reference time is the TLE epoch (see ntn_geometry.py for why).

Output (results/geometry/):
    constellation_schedule.csv   time, satellite, elevation, range, delay, time left
    visibility_summary.csv       one row per pass: rise / culmination / set
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass

from skyfield.api import load, wgs84

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ntn_geometry import (GND_ALT_M, GND_LAT, GND_LON, MIN_PASS_ELEVATION_DEG,  # noqa: E402
                          SHELL_ALTITUDE_MAX_KM, SHELL_ALTITUDE_MIN_KM,
                          TLE_PATH, one_way_delay_ms)

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_HERE, "..", "results", "geometry")

# How long a stretch of constellation activity to characterise, and how finely.
WINDOW_MINUTES = 60.0
SAMPLE_SECONDS = 5.0

# The full TLE set is ~10k satellites; scanning all of them costs minutes and is
# unnecessary, since only a handful pass overhead in any given window.
SCAN_SATELLITES = 2000

FIELDS = ["t_utc", "elapsed_s", "satellite", "elevation_deg", "slant_range_km",
          "one_way_ms", "rtt_ms", "remaining_service_s", "rising"]


@dataclass
class Pass:
    name: str
    rise_tt: float
    culm_tt: float
    set_tt: float
    culm_elev: float

    @property
    def duration_min(self) -> float:
        return (self.set_tt - self.rise_tt) * 24 * 60


def find_passes(max_satellites: int = SCAN_SATELLITES,
                window_minutes: float = WINDOW_MINUTES):
    ts = load.timescale()
    sats = load.tle_file(TLE_PATH)
    if not sats:
        sys.exit("No satellites parsed from TLE file")

    ground = wgs84.latlon(GND_LAT, GND_LON, elevation_m=GND_ALT_M)

    epoch_jd = sorted(s.epoch.tt for s in sats)[len(sats) // 2]
    t0 = ts.tt_jd(epoch_jd)
    t1 = ts.tt_jd(epoch_jd + window_minutes / (24 * 60))

    print(f"Reference epoch : {t0.utc_strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Window          : {window_minutes:.0f} min")
    print(f"Ground station  : {GND_LAT:.4f}, {GND_LON:.4f}  (Montreal)")
    print(f"Scanning {max_satellites} satellites (shell "
          f"{SHELL_ALTITUDE_MIN_KM:.0f}-{SHELL_ALTITUDE_MAX_KM:.0f} km, "
          f"min elevation {MIN_PASS_ELEVATION_DEG:.0f} deg)...")

    passes: list[Pass] = []
    used = {}

    for sat in sats[:max_satellites]:
        try:
            times, events = sat.find_events(
                ground, t0, t1, altitude_degrees=MIN_PASS_ELEVATION_DEG)
        except Exception:
            continue
        if len(times) == 0:
            continue

        rise = culm = None
        for t, ev in zip(times, events):
            if ev == 0:
                rise = t
            elif ev == 1:
                culm = t
            elif ev == 2 and rise is not None and culm is not None:
                height_km = wgs84.height_of(sat.at(culm)).km
                if not (SHELL_ALTITUDE_MIN_KM <= height_km <= SHELL_ALTITUDE_MAX_KM):
                    rise = culm = None
                    continue
                alt, _, _ = (sat - ground).at(culm).altaz()
                passes.append(Pass(sat.name, rise.tt, culm.tt, t.tt, alt.degrees))
                used[sat.name] = sat
                rise = culm = None

    passes.sort(key=lambda p: p.rise_tt)
    return used, passes, t0, t1


def sample_visibility(used: dict, passes: list[Pass], t0, t1) -> list[dict]:
    """Elevation, delay and remaining service time for every visible satellite."""
    ts = load.timescale()
    ground = wgs84.latlon(GND_LAT, GND_LON, elevation_m=GND_ALT_M)

    step_jd = SAMPLE_SECONDS / 86400.0
    n = int((t1.tt - t0.tt) / step_jd)

    rows = []
    for i in range(n + 1):
        t = ts.tt_jd(t0.tt + i * step_jd)
        stamp = t.utc_strftime("%Y-%m-%d %H:%M:%S")
        elapsed = i * SAMPLE_SECONDS

        for p in passes:
            if not (p.rise_tt <= t.tt <= p.set_tt):
                continue
            sat = used.get(p.name)
            if sat is None:
                continue
            alt, _, dist = (sat - ground).at(t).altaz()
            if alt.degrees < MIN_PASS_ELEVATION_DEG:
                continue

            rows.append({
                "t_utc": stamp,
                "elapsed_s": round(elapsed, 1),
                "satellite": p.name,
                "elevation_deg": round(alt.degrees, 4),
                "slant_range_km": round(dist.km, 3),
                "one_way_ms": round(one_way_delay_ms(dist.km), 4),
                "rtt_ms": round(2 * one_way_delay_ms(dist.km), 4),
                # Time until this satellite drops below the minimum elevation.
                "remaining_service_s": round((p.set_tt - t.tt) * 86400, 1),
                # Still climbing: useful for preferring a rising candidate over
                # one that is already descending.
                "rising": int(t.tt < p.culm_tt),
            })
    return rows


def write_outputs(passes: list[Pass], rows: list[dict], t0) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    sched = os.path.join(OUT_DIR, "constellation_schedule.csv")
    with open(sched, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    summ = os.path.join(OUT_DIR, "visibility_summary.csv")
    with open(summ, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["satellite", "rise_elapsed_s", "culmination_elapsed_s",
                    "set_elapsed_s", "duration_min", "culmination_elev_deg"])
        for p in passes:
            w.writerow([
                p.name,
                round((p.rise_tt - t0.tt) * 86400, 1),
                round((p.culm_tt - t0.tt) * 86400, 1),
                round((p.set_tt - t0.tt) * 86400, 1),
                round(p.duration_min, 2),
                round(p.culm_elev, 2),
            ])

    print(f"\nWrote:\n  {sched}\n  {summ}")


def report(passes: list[Pass], rows: list[dict], t0) -> None:
    print(f"\n{'satellite':<22}{'rise':>8}{'culm':>8}{'set':>8}{'dur':>7}{'max elev':>10}")
    print(f"{'':<22}{'(s)':>8}{'(s)':>8}{'(s)':>8}{'(min)':>7}{'(deg)':>10}")
    for p in passes:
        print(f"{p.name:<22}"
              f"{(p.rise_tt - t0.tt)*86400:8.0f}"
              f"{(p.culm_tt - t0.tt)*86400:8.0f}"
              f"{(p.set_tt - t0.tt)*86400:8.0f}"
              f"{p.duration_min:7.1f}"
              f"{p.culm_elev:10.1f}")

    # A handover chain is only possible where two passes overlap: the rising
    # satellite must already be visible before the serving one sets.
    overlaps = sum(1 for a, b in zip(passes, passes[1:]) if b.rise_tt < a.set_tt)

    by_time: dict[float, int] = {}
    for r in rows:
        by_time[r["elapsed_s"]] = by_time.get(r["elapsed_s"], 0) + 1
    max_visible = max(by_time.values()) if by_time else 0
    never_covered = sum(1 for v in by_time.values() if v == 0)
    gaps = int((WINDOW_MINUTES * 60 / SAMPLE_SECONDS) + 1) - len(by_time)

    print(f"\nPasses                     : {len(passes)}")
    print(f"Consecutive overlapping    : {overlaps}")
    print(f"Max simultaneously visible : {max_visible}")
    print(f"Samples with no satellite  : {gaps + never_covered}")

    if overlaps == 0:
        print("\nWARNING: no overlapping passes. A handover chain is impossible in\n"
              "         this window -- widen WINDOW_MINUTES or lower\n"
              "         MIN_PASS_ELEVATION_DEG.")
    if gaps + never_covered > 0:
        print("\nNOTE: coverage is not continuous. Stretches with no visible\n"
              "      satellite are real for a single ground station and a partial\n"
              "      TLE scan; the orchestrator must hold the current cell across\n"
              "      them rather than treating them as handover failures.")


def main() -> None:
    used, passes, t0, t1 = find_passes()
    if not passes:
        sys.exit("No passes found. Widen the window or scan more satellites.")

    rows = sample_visibility(used, passes, t0, t1)
    report(passes, rows, t0)
    write_outputs(passes, rows, t0)

    names = sorted({r["satellite"] for r in rows})
    print(f"Sampled {len(rows)} visibility points across {len(names)} satellites "
          f"at {SAMPLE_SECONDS:.0f} s resolution")


if __name__ == "__main__":
    main()
