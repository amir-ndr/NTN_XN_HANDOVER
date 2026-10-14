#!/usr/bin/env python3
"""
Testbed control helpers: drive handovers, measure interruption, collect results.

Assumes the topology from UERANSIM_XN/config/xn3 is already running (see README.md):
gNBs in the main namespace, the UE inside the ue1 network namespace.
Must be run as root -- it shells out to `ip netns exec` and `tc`.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

# ── Testbed layout ────────────────────────────────────────────────────────────

# Where the modified UERANSIM (UERANSIM_XN) is checked out and built.
# Default: ~/UERANSIM_XN of the user who ran sudo (under sudo, "~" is /root).
_HOME = os.path.expanduser("~" + os.environ.get("SUDO_USER", ""))
UERANSIM_DIR = os.environ.get("UERANSIM_DIR", os.path.join(_HOME, "UERANSIM_XN"))
NR_CLI = os.path.join(UERANSIM_DIR, "build", "nr-cli")
# The per-handover timing file written by the TARGET gNB. Its path is compiled
# into UERANSIM_XN (src/gnb/ngap/pathswitch.cpp); this must be the same path.
HO_CSV = os.environ.get("XN_HO_CSV", "/tmp/xn_ho_latency.csv")

NETNS = "ue1"
UE_TUN = "uesimtun0"
PING_TARGET = "10.45.0.1"          # UPF end of the tunnel

GNBS = {
    1: {"cli": "UERANSIM-gnb-999-70-1", "xn": "127.0.0.1"},
    2: {"cli": "UERANSIM-gnb-999-70-2", "xn": "127.0.0.2"},
    3: {"cli": "UERANSIM-gnb-999-70-3", "xn": "127.0.0.3"},
}

_HERE = os.path.dirname(os.path.abspath(__file__))
NETEM = os.path.join(_HERE, "netem.sh")


# ── Results ───────────────────────────────────────────────────────────────────

@dataclass
class HandoverResult:
    ok: bool = False
    error: str = ""
    source_gnb: int = 0
    target_gnb: int = 0

    # From the gNB's own latency row (written by the target).
    sst: int = 0
    ue_arrival_ms: float = float("nan")
    psw_ms: float = float("nan")
    total_ms: float = float("nan")

    # From the concurrent ping.
    ping_sent: int = 0
    ping_recv: int = 0
    ping_lost: int = 0
    gap_packets: int = 0          # longest run of consecutive missing packets
    gap_ms: float = float("nan")  # outage measured from reply timestamps
    ping_interval_actual_ms: float = float("nan")  # spacing ping really achieved
    ping_rtt_avg_ms: float = float("nan")

    extra: dict = field(default_factory=dict)


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def cli(gnb: int, command: str, timeout: float = 30.0) -> str:
    """Run an nr-cli command against a gNB."""
    _, out = _run([NR_CLI, GNBS[gnb]["cli"], "--exec", command], timeout)
    return out.strip()


def find_serving_gnb() -> tuple[int, int] | tuple[None, None]:
    """
    Locate the UE: returns (gnb_index, ue_id).

    The gNBs are queried directly rather than tracked in the driver, so the
    sweep stays correct even if a handover fails and the UE does not move.
    """
    for idx in GNBS:
        out = cli(idx, "ue-list")
        m = re.search(r"ue-id:\s*(\d+)", out)
        if m:
            return idx, int(m.group(1))
    return None, None


def set_delay(link: str, one_way_ms: float) -> None:
    rc, out = _run(["sudo", NETEM, "set", link, f"{one_way_ms:.3f}"], timeout=30)
    if rc != 0:
        raise RuntimeError(f"netem set {link} {one_way_ms} failed: {out}")


def clear_delay() -> None:
    _run(["sudo", NETEM, "clear"], timeout=30)


# ── Interruption measurement ──────────────────────────────────────────────────

_SEQ_RE = re.compile(r"icmp_seq=(\d+)")
# With ping -D each reply is prefixed with a unix timestamp.
_STAMPED_RE = re.compile(r"\[(\d+\.\d+)\][^\n]*icmp_seq=(\d+)")
_SUMMARY_RE = re.compile(
    r"(\d+) packets transmitted, (\d+) received.*?time (\d+)ms", re.S)
_RTT_RE = re.compile(r"= [\d.]+/([\d.]+)/")


def _parse_ping(output: str, interval_s: float) -> dict:
    """
    Extract loss and the outage from ping output.

    The outage is taken from REPLY TIMESTAMPS (ping -D), not from a packet count
    multiplied by the requested interval. Ping does not reliably achieve a 1 ms
    spacing once a link delay is injected -- it degrades towards the round-trip
    rate -- so `gap_packets * nominal_interval` understates the outage by the
    ratio between requested and achieved spacing (observed 5-7x). Measuring the
    largest inter-arrival gap directly is immune to that.

    The largest gap is used rather than total loss because a handover appears as
    one unbroken stretch of silence, whereas total loss could also fold in
    unrelated scattered drops.
    """
    seqs = sorted(int(m) for m in _SEQ_RE.findall(output))
    sent = recv = 0
    m = _SUMMARY_RE.search(output)
    if m:
        sent, recv = int(m.group(1)), int(m.group(2))

    rtt_avg = float("nan")
    r = _RTT_RE.search(output)
    if r:
        rtt_avg = float(r.group(1))

    longest = 0
    if len(seqs) >= 2:
        for a, b in zip(seqs, seqs[1:]):
            longest = max(longest, b - a - 1)

    # Outage = (contiguous packets missed) x (spacing actually achieved).
    #
    # Both halves matter and both were got wrong before:
    #   - the COUNT must come from sequence numbers, because the largest
    #     inter-arrival time picks up the worst jitter spike over ~900 samples,
    #     which is usually not the handover (observed 46 ms "outages" on 9 ms
    #     handovers);
    #   - the TIME BASE must be the achieved spacing, not the requested one,
    #     because ping degrades towards the round-trip rate once a delay is
    #     injected (1 ms requested, 5-8 ms achieved).
    #
    # Resolution is therefore one achieved interval, i.e. +/- 5-8 ms under
    # injected delay. Outages below that cannot be distinguished from zero.
    stamps = sorted(float(t) for t, _ in _STAMPED_RE.findall(output))
    achieved_interval_ms = float("nan")
    if len(stamps) >= 3:
        deltas = sorted((b - a) * 1000.0 for a, b in zip(stamps, stamps[1:]))
        achieved_interval_ms = deltas[len(deltas) // 2]   # median, jitter-robust

    gap_ms = (longest * achieved_interval_ms
              if achieved_interval_ms == achieved_interval_ms
              else longest * interval_s * 1000.0)

    return {
        "ping_sent": sent,
        "ping_recv": recv,
        "ping_lost": max(0, sent - recv),
        "gap_packets": longest,
        "gap_ms": gap_ms,
        "ping_interval_actual_ms": achieved_interval_ms,
        "ping_rtt_avg_ms": rtt_avg,
    }


def start_ping(count: int, interval_s: float,
               deadline_s: float) -> tuple[subprocess.Popen, object]:
    """
    Start ping with a hard DEADLINE, capturing to a temporary file.

    Two failure modes are being avoided here, both found the hard way:

    1. A pipe deadlocks. The driver does not read it while waiting out the lead
       time and the handover, so ping blocks once the ~64 KB pipe buffer fills
       (2200 per-packet lines is ~128 KB).

    2. `-c count` alone makes ping's runtime UNBOUNDED: after sending it lingers
       for outstanding replies, and an injected link delay stretches that tail
       until the driver's timeout fires. Ping is then killed before printing its
       summary, which zeroes ping_sent/ping_recv and voids the RTT.

    `-w` bounds the total run regardless of delay or loss, so the capture always
    contains the summary line.
    """
    tmp = tempfile.NamedTemporaryFile(mode="w+", suffix=".ping", delete=False)
    proc = subprocess.Popen(
        ["ip", "netns", "exec", NETNS, "ping", "-I", UE_TUN,
         "-i", str(interval_s), "-c", str(count),
         "-w", str(int(deadline_s)), "-D", PING_TARGET],
        stdout=tmp, stderr=subprocess.STDOUT, text=True)
    return proc, tmp


# ── Handover ──────────────────────────────────────────────────────────────────

def _csv_rows() -> list[str]:
    if not os.path.exists(HO_CSV):
        return []
    with open(HO_CSV) as f:
        return [ln.strip() for ln in f if ln.strip()]


def _wait_for_new_row(prev: int, timeout: float = 15.0) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _csv_rows()
        if len(rows) > prev:
            return rows[-1]
        time.sleep(0.05)
    return None


def do_handover(target_gnb: int | None = None,
                measure_interruption: bool = True,
                ping_interval_s: float = 0.002,
                ping_lead_s: float = 0.7,
                ping_tail_s: float = 1.5) -> HandoverResult:
    """
    Hand the UE over to `target_gnb` (default: the other gNB of the 1<->2 pair)
    with a ping running across the switch.

    The ping is started before the trigger and kept running afterwards so the
    outage is bracketed by known-good packets on both sides; otherwise a gap at
    the very start or end of the capture could not be measured.
    """
    src, ue_id = find_serving_gnb()
    if src is None:
        return HandoverResult(ok=False, error="UE not found on any gNB")

    if target_gnb is None:
        target_gnb = 2 if src == 1 else 1
    if target_gnb == src:
        return HandoverResult(ok=False, error=f"UE already on gnb{src}")

    res = HandoverResult(source_gnb=src, target_gnb=target_gnb)

    proc = tmp = None
    ping_count = 0
    if measure_interruption:
        total_s = ping_lead_s + ping_tail_s
        ping_count = int(total_s / ping_interval_s)
        # Deadline generous enough to cover the send phase plus a delayed tail,
        # but bounded so a stalled ping cannot stretch the experiment.
        ping_deadline = max(3.0, total_s + 3.0)
        proc, tmp = start_ping(ping_count, ping_interval_s, ping_deadline)
        time.sleep(ping_lead_s)

    prev_rows = len(_csv_rows())
    out = cli(src, f"xn-handover {ue_id} {GNBS[target_gnb]['xn']}")

    if "triggered" not in out.lower():
        if proc:
            proc.kill()
        return HandoverResult(ok=False, error=f"trigger rejected: {out}",
                              source_gnb=src, target_gnb=target_gnb)

    row = _wait_for_new_row(prev_rows)

    if proc is not None:
        # Ping needs count*interval to send, plus the round trip for the last
        # replies. Injected delay lengthens that tail, so allow generous margin
        # -- a premature kill loses the summary line and voids the loss counts.
        # ping now self-terminates at its own deadline; allow a small margin.
        budget = ping_deadline + 5.0
        killed = False
        try:
            proc.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            killed = True

        tmp.flush()
        try:
            with open(tmp.name) as f:
                ping_out = f.read()
        except OSError:
            ping_out = ""
        finally:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        for k, v in _parse_ping(ping_out, ping_interval_s).items():
            setattr(res, k, v)

        # The summary line is what carries sent/received. Without it the loss
        # counts are meaningless, so say so rather than reporting zeros.
        if res.ping_sent == 0:
            res.extra["ping_incomplete"] = True
            res.error = (res.error + "; " if res.error else "") + \
                        ("ping killed before summary" if killed
                         else "ping summary missing")

    if row is None:
        res.ok = False
        res.error = "no latency row appeared (handover did not complete)"
        return res

    parts = row.split(",")
    if len(parts) < 4:
        res.ok = False
        res.error = f"malformed row: {row}"
        return res

    res.sst = int(parts[0])
    res.ue_arrival_ms = float(parts[1])
    res.psw_ms = float(parts[2])
    res.total_ms = float(parts[3])
    res.ok = True
    return res


def ensure_csv_header() -> None:
    """The gNB appends rows only; make sure the header exists."""
    rows = _csv_rows()
    if not rows or not rows[0].startswith("sst"):
        with open(HO_CSV, "w") as f:
            f.write("sst,ue_arrival_ms,psw_ms,total_ms\n")


def preflight() -> list[str]:
    """Check the testbed is ready; returns a list of problems (empty == ready)."""
    problems = []

    if os.geteuid() != 0:
        problems.append("must be run as root (sudo)")

    if not os.path.exists(NR_CLI):
        problems.append(f"nr-cli not found at {NR_CLI}")

    rc, _ = _run(["ip", "netns", "list"])
    if rc != 0:
        problems.append("cannot list network namespaces")

    rc, out = _run(["ip", "netns", "exec", NETNS, "ip", "addr", "show", UE_TUN])
    if rc != 0 or "inet " not in out:
        problems.append(f"{UE_TUN} not up inside netns '{NETNS}' -- is the UE running?")

    src, ue_id = find_serving_gnb()
    if src is None:
        problems.append("UE is not registered on any gNB")

    return problems
