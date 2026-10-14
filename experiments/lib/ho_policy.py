#!/usr/bin/env python3
"""
Satellite selection policies and offline handover-decision simulation.

Reads the constellation schedule (lib/ntn_constellation.py) and works out WHEN a
handover should fire and to WHICH satellite, under several policies from the LEO
literature. Nothing here touches the testbed: this produces the decision
timeline that the live orchestrator later replays.

POLICIES
    max_elevation        always serve on the highest-elevation visible satellite.
                         Best link quality, but known to cause frequent handovers
                         as satellites overtake one another.
    elevation_threshold  hold the serving satellite until its elevation falls
                         below a threshold, then take the best candidate. Fewer
                         handovers, but can switch late: two passes can share an
                         elevation while having very different time left.
    max_service_time     hold until the serving satellite is close to setting,
                         then take the candidate with the longest remaining
                         visibility. Fewest handovers in the literature.

RELATION TO 3GPP
    3GPP defines no elevation event. The NTN-specific Rel-17 conditional
    handover triggers are CondEvent D1 (distance to serving above one threshold
    AND distance to candidate below another) and CondEvent T1 (time based).
    Elevation and slant range are monotonically related, so an elevation
    threshold is equivalent to a D1-style distance condition, and
    max_service_time is a T1-style time condition. The policies below are
    therefore expressible as D1/T1, which is how they should be described.

STABILITY CONTROLS (all three policies)
    hysteresis   a candidate must be better than the serving cell by a margin,
                 not merely better. Without it, two satellites near equal
                 elevation trigger repeated ping-pong handovers.
    time_to_trigger  the condition must hold continuously for this long before
                 firing, so a momentary crossing does not cause a handover.
    min_dwell    a floor on the time between consecutive handovers.
    lead_time    fire early enough that the handover COMPLETES before the
                 serving link becomes unusable. This should be set from the
                 measured handover latency (experiment E2) plus margin -- it is
                 the direct link between the cost measurements and this policy.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_CSV = os.path.join(_HERE, "..", "results", "geometry",
                            "constellation_schedule.csv")

# A satellite below this is not usable; matches the schedule generator.
MIN_ELEVATION_DEG = 10.0


@dataclass
class PolicyConfig:
    name: str = "max_service_time"

    # Stability controls
    hysteresis_deg: float = 5.0
    time_to_trigger_s: float = 5.0
    min_dwell_s: float = 20.0

    # Fire this long before the serving satellite becomes unusable. Must exceed
    # the handover latency measured in E2, or the link dies mid-procedure.
    lead_time_s: float = 15.0

    # elevation_threshold only
    elevation_threshold_deg: float = 25.0


@dataclass
class Visible:
    satellite: str
    elevation_deg: float
    slant_range_km: float
    one_way_ms: float
    remaining_service_s: float
    rising: bool


@dataclass
class Handover:
    t_s: float
    from_sat: str
    to_sat: str
    from_elev: float
    to_elev: float
    from_one_way_ms: float
    to_one_way_ms: float
    reason: str


@dataclass
class SimResult:
    policy: str
    handovers: list[Handover] = field(default_factory=list)
    serving_trace: list[dict] = field(default_factory=list)
    outage_s: float = 0.0
    acquisitions: int = 0     # (re)selections with no serving cell to leave


def load_schedule(path: str = SCHEDULE_CSV,
                  window_s: float | None = None,
                  offset_s: float = 0.0) -> dict[float, list[Visible]]:
    if not os.path.exists(path):
        raise SystemExit(f"Missing {path}\nRun:  python3 lib/ntn_constellation.py")

    by_time: dict[float, list[Visible]] = defaultdict(list)
    t_min = None
    with open(path) as f:
        for r in csv.DictReader(f):
            t = float(r["elapsed_s"])
            if t_min is None:
                t_min = t
            # Slice [offset, offset + window) so independent stretches of the
            # constellation can be sampled without regenerating the geometry.
            rel = t - t_min - offset_s
            if rel < 0:
                continue
            if window_s is not None and rel > window_s:
                continue
            by_time[t].append(Visible(
                satellite=r["satellite"],
                elevation_deg=float(r["elevation_deg"]),
                slant_range_km=float(r["slant_range_km"]),
                one_way_ms=float(r["one_way_ms"]),
                remaining_service_s=float(r["remaining_service_s"]),
                rising=r["rising"] == "1",
            ))
    return dict(sorted(by_time.items()))


# ── Candidate choice, per policy ──────────────────────────────────────────────

def _best_candidate(cfg: PolicyConfig, visible: list[Visible],
                    serving: Visible | None) -> Visible | None:
    """Pick the preferred satellite under the policy, ignoring stability rules."""
    usable = [v for v in visible if v.elevation_deg >= MIN_ELEVATION_DEG]
    if not usable:
        return None

    if cfg.name == "max_elevation":
        return max(usable, key=lambda v: v.elevation_deg)

    if cfg.name == "elevation_threshold":
        # Prefer a satellite that will last, breaking ties on elevation.
        return max(usable, key=lambda v: (v.remaining_service_s, v.elevation_deg))

    if cfg.name == "max_service_time":
        return max(usable, key=lambda v: v.remaining_service_s)

    raise ValueError(f"unknown policy {cfg.name}")


def _wants_handover(cfg: PolicyConfig, serving: Visible,
                    cand: Visible) -> tuple[bool, str]:
    """Does the policy consider a switch warranted right now?"""
    if cand.satellite == serving.satellite:
        return False, ""

    if cfg.name == "max_elevation":
        if cand.elevation_deg > serving.elevation_deg + cfg.hysteresis_deg:
            return True, "candidate higher by hysteresis margin"
        return False, ""

    if cfg.name == "elevation_threshold":
        if serving.elevation_deg < cfg.elevation_threshold_deg:
            if cand.elevation_deg > serving.elevation_deg + cfg.hysteresis_deg:
                return True, "serving below elevation threshold"
        return False, ""

    if cfg.name == "max_service_time":
        if serving.remaining_service_s <= cfg.lead_time_s:
            if cand.remaining_service_s > serving.remaining_service_s:
                return True, "serving about to set"
        return False, ""

    raise ValueError(f"unknown policy {cfg.name}")


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate(cfg: PolicyConfig, schedule: dict[float, list[Visible]]) -> SimResult:
    res = SimResult(policy=cfg.name)

    serving_name: str | None = None
    last_ho_t = -1e9
    trigger_since: float | None = None
    trigger_target: str | None = None

    times = sorted(schedule)
    dt = (times[1] - times[0]) if len(times) > 1 else 5.0

    for t in times:
        visible = schedule[t]
        by_name = {v.satellite: v for v in visible}
        serving = by_name.get(serving_name) if serving_name else None

        # Serving satellite gone (set, or never selected): must acquire.
        if serving is None or serving.elevation_deg < MIN_ELEVATION_DEG:
            cand = _best_candidate(cfg, visible, None)
            if cand is None:
                res.outage_s += dt
                serving_name = None
                res.serving_trace.append({"t_s": t, "satellite": "",
                                          "elevation_deg": float("nan"),
                                          "one_way_ms": float("nan")})
                continue

            if serving_name is not None:
                # Lost the serving cell before handing over: a late handover.
                res.handovers.append(Handover(
                    t_s=t, from_sat=serving_name, to_sat=cand.satellite,
                    from_elev=serving.elevation_deg if serving else float("nan"),
                    to_elev=cand.elevation_deg,
                    from_one_way_ms=serving.one_way_ms if serving else float("nan"),
                    to_one_way_ms=cand.one_way_ms,
                    reason="serving lost (late handover)"))
            else:
                res.acquisitions += 1

            serving_name = cand.satellite
            last_ho_t = t
            trigger_since = trigger_target = None
            serving = cand

        else:
            cand = _best_candidate(cfg, visible, serving)
            fire, reason = (False, "")
            if cand is not None:
                fire, reason = _wants_handover(cfg, serving, cand)

            if not fire:
                trigger_since = trigger_target = None
            else:
                # Condition must hold continuously, on the same target.
                if trigger_target != cand.satellite:
                    trigger_since, trigger_target = t, cand.satellite

                held = t - (trigger_since if trigger_since is not None else t)
                if held >= cfg.time_to_trigger_s and (t - last_ho_t) >= cfg.min_dwell_s:
                    res.handovers.append(Handover(
                        t_s=t, from_sat=serving.satellite, to_sat=cand.satellite,
                        from_elev=serving.elevation_deg, to_elev=cand.elevation_deg,
                        from_one_way_ms=serving.one_way_ms,
                        to_one_way_ms=cand.one_way_ms,
                        reason=reason))
                    serving_name = cand.satellite
                    last_ho_t = t
                    trigger_since = trigger_target = None
                    serving = cand

        res.serving_trace.append({
            "t_s": t,
            "satellite": serving.satellite,
            "elevation_deg": serving.elevation_deg,
            "one_way_ms": serving.one_way_ms,
        })

    return res


def summarise(res: SimResult, duration_s: float) -> dict:
    elevs = [s["elevation_deg"] for s in res.serving_trace
             if s["elevation_deg"] == s["elevation_deg"]]
    delays = [s["one_way_ms"] for s in res.serving_trace
              if s["one_way_ms"] == s["one_way_ms"]]
    late = sum(1 for h in res.handovers if "late" in h.reason)

    return {
        "policy": res.policy,
        "handovers": len(res.handovers),
        "handovers_per_min": round(len(res.handovers) / (duration_s / 60), 2),
        "late_handovers": late,
        "outage_s": round(res.outage_s, 1),
        "mean_serving_elev_deg": round(sum(elevs) / len(elevs), 2) if elevs else 0.0,
        "min_serving_elev_deg": round(min(elevs), 2) if elevs else 0.0,
        "mean_serving_one_way_ms": round(sum(delays) / len(delays), 3) if delays else 0.0,
        "max_serving_one_way_ms": round(max(delays), 3) if delays else 0.0,
    }
