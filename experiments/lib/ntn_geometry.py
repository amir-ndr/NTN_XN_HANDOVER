#!/usr/bin/env python3
"""
NTN geometry: elevation angle -> slant range -> propagation delay.

Produces the delay values that are injected into the testbed with netem, derived
from real Starlink ephemeris (starlink.tle) over a Montreal ground station.

WHY THE TLE EPOCH IS USED AS THE REFERENCE TIME
    A TLE is only accurate for a few days either side of its epoch; propagating
    it weeks or months forward yields fictional geometry. starlink.tle has an
    epoch in late April 2026, so all passes are computed AT that epoch rather
    than at wall-clock "now". The geometry is therefore real and the result is
    reproducible offline, which is what matters for the measurements.

MODELLING ASSUMPTION (stated explicitly because it matters for interpretation)
    A single elevation angle is used per run. Physically the service link
    (UE->satellite) and the feeder link (satellite->gateway) have their own
    geometries and therefore their own elevation angles. Treating them as equal
    is the standard first-order simplification: it means "the satellite is at
    elevation E as seen from both endpoints". Where the two need to differ, run
    the sweep per link rather than assuming one value covers both.

Outputs (results/geometry/):
    pass_profile.csv     the sampled real pass: time, elevation, range, delay
    elevation_points.csv the sweep points actually used by the experiments
    reference_orbits.csv LEO/MEO/GEO comparison values
"""

from __future__ import annotations

import csv
import math
import os
import sys
from dataclasses import dataclass

from skyfield.api import load, wgs84

# ── Configuration ─────────────────────────────────────────────────────────────

# Ground station: Montreal
GND_LAT = 45.5017
GND_LON = -73.5673
GND_ALT_M = 50.0

# Elevation angles swept by the experiments. 90 deg is the satellite overhead
# (shortest range, lowest delay); 10 deg is near the horizon, which is the usual
# minimum elevation for an NTN service link and gives the longest range.
TARGET_ELEVATIONS_DEG = [10, 15, 20, 30, 45, 60, 90]

# Minimum elevation that counts as a usable pass.
MIN_PASS_ELEVATION_DEG = 10.0

# Only consider satellites in Starlink's main operational shell. The TLE set
# also contains lowered and deorbiting satellites (some below 400 km); picking
# one of those would understate the delay of a representative LEO link.
SHELL_ALTITUDE_MIN_KM = 500.0
SHELL_ALTITUDE_MAX_KM = 600.0

SPEED_OF_LIGHT_KM_S = 299792.458
EARTH_RADIUS_KM = 6371.0

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
TLE_PATH = os.path.join(REPO_ROOT, "starlink.tle")
OUT_DIR = os.path.join(_HERE, "..", "results", "geometry")


# ── Core geometry ─────────────────────────────────────────────────────────────

def one_way_delay_ms(slant_range_km: float) -> float:
    """Propagation delay for a given slant range."""
    return slant_range_km / SPEED_OF_LIGHT_KM_S * 1000.0


def analytic_slant_range_km(elevation_deg: float, altitude_km: float) -> float:
    """
    Slant range to a satellite at a given elevation, spherical-Earth geometry:

        d = sqrt(R^2 sin^2(e) + h^2 + 2Rh) - R sin(e)

    Used only as a cross-check against the ephemeris-derived range; the
    experiments use the real values from the pass.
    """
    e = math.radians(elevation_deg)
    r = EARTH_RADIUS_KM
    h = altitude_km
    return math.sqrt((r * math.sin(e)) ** 2 + h * h + 2 * r * h) - r * math.sin(e)


@dataclass
class Sample:
    """One instant of a real satellite pass."""
    t_utc: str
    elevation_deg: float
    slant_range_km: float

    @property
    def one_way_ms(self) -> float:
        return one_way_delay_ms(self.slant_range_km)

    @property
    def rtt_ms(self) -> float:
        return 2.0 * self.one_way_ms


# ── Pass finding ──────────────────────────────────────────────────────────────

def find_best_pass(max_satellites: int = 1500, window_hours: float = 6.0):
    """
    Find the highest-culminating pass over the ground station, searching from the
    TLE epoch forward. Returns (satellite, rise_time, set_time, culmination_deg).

    Satellites are scanned until a near-overhead pass is found; a high
    culmination is wanted so the sweep can reach 90 deg elevation using a single
    real pass rather than stitching several together.
    """
    if not os.path.exists(TLE_PATH):
        sys.exit(f"TLE file not found: {TLE_PATH}")

    ts = load.timescale()
    sats = load.tle_file(TLE_PATH)
    if not sats:
        sys.exit("No satellites parsed from TLE file")

    ground = wgs84.latlon(GND_LAT, GND_LON, elevation_m=GND_ALT_M)

    # Anchor the search at the TLE epoch (see module docstring).
    epoch_jd = sorted(s.epoch.tt for s in sats)[len(sats) // 2]
    t0 = ts.tt_jd(epoch_jd)
    t1 = ts.tt_jd(epoch_jd + window_hours / 24.0)

    print(f"TLE epoch (median): {t0.utc_strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Searching {window_hours:.0f} h window over "
          f"{GND_LAT:.4f}, {GND_LON:.4f} across up to {max_satellites} satellites...")

    best = None
    for sat in sats[:max_satellites]:
        try:
            times, events = sat.find_events(
                ground, t0, t1, altitude_degrees=MIN_PASS_ELEVATION_DEG)
        except Exception:
            continue

        for t, ev in zip(times, events):
            if ev != 1:  # 1 == culmination
                continue

            # Keep only satellites in the operational shell.
            height_km = wgs84.height_of(sat.at(t)).km
            if not (SHELL_ALTITUDE_MIN_KM <= height_km <= SHELL_ALTITUDE_MAX_KM):
                continue

            alt, _, _ = (sat - ground).at(t).altaz()
            if best is None or alt.degrees > best[3]:
                best = (sat, t, t, alt.degrees)

        # Near-overhead pass found; no need to keep scanning.
        if best is not None and best[3] >= 85.0:
            break

    if best is None:
        sys.exit("No pass found above the minimum elevation. "
                 "Try a longer window or more satellites.")

    sat, t_culm, _, culm_deg = best

    # Expand around culmination to the horizon crossings of this pass.
    times, events = sat.find_events(
        ground,
        ts.tt_jd(t_culm.tt - 0.5 / 24.0),
        ts.tt_jd(t_culm.tt + 0.5 / 24.0),
        altitude_degrees=MIN_PASS_ELEVATION_DEG)

    rise = set_ = None
    for t, ev in zip(times, events):
        if ev == 0 and t.tt <= t_culm.tt:
            rise = t
        elif ev == 2 and t.tt >= t_culm.tt and set_ is None:
            set_ = t

    if rise is None or set_ is None:
        sys.exit("Could not bracket the pass between rise and set.")

    print(f"Selected: {sat.name}  culmination {culm_deg:.1f} deg")
    return sat, rise, set_, culm_deg


def sample_pass(sat, t_rise, t_set, n: int = 400) -> list[Sample]:
    """Sample elevation and slant range uniformly across the pass."""
    ts = load.timescale()
    ground = wgs84.latlon(GND_LAT, GND_LON, elevation_m=GND_ALT_M)

    jd0, jd1 = t_rise.tt, t_set.tt
    out: list[Sample] = []
    for i in range(n):
        t = ts.tt_jd(jd0 + (jd1 - jd0) * i / (n - 1))
        alt, _, dist = (sat - ground).at(t).altaz()
        out.append(Sample(
            t_utc=t.utc_strftime("%Y-%m-%d %H:%M:%S"),
            elevation_deg=alt.degrees,
            slant_range_km=dist.km,
        ))
    return out


def pick_elevation_points(samples: list[Sample],
                          targets: list[float]) -> list[tuple[float, Sample]]:
    """
    For each target elevation, take the sample from the real pass closest to it.

    The ascending half of the pass is used so that each target maps to a single
    unambiguous instant (elevation is non-monotonic across a full pass).
    """
    if not samples:
        return []
    culm_idx = max(range(len(samples)), key=lambda i: samples[i].elevation_deg)
    ascending = samples[:culm_idx + 1]

    picked = []
    for target in targets:
        best = min(ascending, key=lambda s: abs(s.elevation_deg - target))
        picked.append((target, best))
    return picked


# ── Output ────────────────────────────────────────────────────────────────────

def write_outputs(sat_name: str, samples: list[Sample],
                  points: list[tuple[float, Sample]]) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    profile = os.path.join(OUT_DIR, "pass_profile.csv")
    with open(profile, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["satellite", "time_utc", "elevation_deg",
                    "slant_range_km", "one_way_ms", "rtt_ms"])
        for s in samples:
            w.writerow([sat_name, s.t_utc, f"{s.elevation_deg:.4f}",
                        f"{s.slant_range_km:.3f}", f"{s.one_way_ms:.4f}",
                        f"{s.rtt_ms:.4f}"])

    pts = os.path.join(OUT_DIR, "elevation_points.csv")
    with open(pts, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["target_elevation_deg", "actual_elevation_deg",
                    "slant_range_km", "one_way_ms", "rtt_ms",
                    "netem_delay_ms_per_side"])
        for target, s in points:
            # netem is applied per direction, so each side carries the one-way
            # delay and the round trip comes out at 2x naturally.
            w.writerow([target, f"{s.elevation_deg:.4f}",
                        f"{s.slant_range_km:.3f}", f"{s.one_way_ms:.4f}",
                        f"{s.rtt_ms:.4f}", f"{s.one_way_ms:.3f}"])

    ref = os.path.join(OUT_DIR, "reference_orbits.csv")
    with open(ref, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["orbit", "altitude_km", "elevation_deg",
                    "slant_range_km", "one_way_ms", "rtt_ms"])
        for name, alt_km in [("LEO", 550.0), ("MEO", 8000.0), ("GEO", 35786.0)]:
            for elev in (90.0, 30.0, 10.0):
                d = analytic_slant_range_km(elev, alt_km)
                w.writerow([name, alt_km, elev, f"{d:.1f}",
                            f"{one_way_delay_ms(d):.3f}",
                            f"{2 * one_way_delay_ms(d):.3f}"])

    print(f"\nWrote:\n  {profile}\n  {pts}\n  {ref}")


def main() -> None:
    sat, t_rise, t_set, culm = find_best_pass()
    samples = sample_pass(sat, t_rise, t_set)
    points = pick_elevation_points(samples, TARGET_ELEVATIONS_DEG)

    alt_km = min(s.slant_range_km for s in samples)
    print(f"\nPass: {sat.name}")
    print(f"  rise {t_rise.utc_strftime('%H:%M:%S')} -> "
          f"set {t_set.utc_strftime('%H:%M:%S')} UTC, culmination {culm:.1f} deg")
    print(f"  minimum slant range {alt_km:.1f} km\n")

    print(f"{'target':>7} {'actual':>8} {'range':>10} {'one-way':>9} {'RTT':>8}")
    print(f"{'(deg)':>7} {'(deg)':>8} {'(km)':>10} {'(ms)':>9} {'(ms)':>8}")
    for target, s in points:
        print(f"{target:7.0f} {s.elevation_deg:8.2f} {s.slant_range_km:10.1f} "
              f"{s.one_way_ms:9.3f} {s.rtt_ms:8.3f}")

    # Cross-check the ephemeris against closed-form geometry at culmination.
    culm_sample = max(samples, key=lambda s: s.elevation_deg)
    analytic = analytic_slant_range_km(culm_sample.elevation_deg, alt_km)
    err = abs(analytic - culm_sample.slant_range_km)
    print(f"\nSanity check at culmination: ephemeris {culm_sample.slant_range_km:.1f} km "
          f"vs analytic {analytic:.1f} km (diff {err:.1f} km)")

    write_outputs(sat.name, samples, points)


if __name__ == "__main__":
    main()
