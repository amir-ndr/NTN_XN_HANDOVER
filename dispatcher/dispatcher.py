#!/usr/bin/env python3
"""
NTN Handover Dispatcher — Baseline 2: Random AMF→SMF→UPF chain selection.

Receives PathSwitchRequest JSON from gnb2's XnTask (port 9000).
Probes all NF chains in parallel, randomly picks one reachable chain,
returns the selection to gnb2, and logs detailed results to terminal.

Protocol: 4-byte big-endian length prefix + UTF-8 JSON body.
"""

import json
import random
import socket
import struct
import threading
import time

# ── Chain registry ────────────────────────────────────────────────────────────
#
#  Each chain represents one AMF→SMF→UPF path in the 5G core.
#  Chain 1 points at the REAL Open5GS AMF (127.0.0.5).
#  Chains 2 & 3 are simulated NF instances with different load profiles.
#  The dispatcher probes each chain's nf_chain.py server to measure
#  current queue backlog and round-trip latency.
#
CHAINS = [
    {
        "id":        1,
        "name":      "Chain-1 [low-load   μ=200 req/s]",
        "amf_addr":  "127.0.0.5",          # real Open5GS
        "probe_host": "127.0.0.1",
        "probe_port": 9101,
    },
    {
        "id":        2,
        "name":      "Chain-2 [med-load   μ=100 req/s]",
        "amf_addr":  "127.0.0.5",          # same physical AMF for now
        "probe_host": "127.0.0.1",
        "probe_port": 9102,
    },
    {
        "id":        3,
        "name":      "Chain-3 [high-load  μ= 50 req/s]",
        "amf_addr":  "127.0.0.5",
        "probe_host": "127.0.0.1",
        "probe_port": 9103,
    },
]

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 9000


# ── Wire helpers ──────────────────────────────────────────────────────────────

def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def wire_send_recv(host, port, payload, timeout=3.0):
    """Send JSON, receive JSON over length-prefixed TCP. Returns (dict|None, latency_ms)."""
    raw = json.dumps(payload).encode()
    t0  = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(struct.pack("!I", len(raw)) + raw)
            hdr = _recv_exact(s, 4)
            if not hdr:
                return None, -1.0
            rlen = struct.unpack("!I", hdr)[0]
            body = _recv_exact(s, rlen)
            if not body:
                return None, -1.0
            lat = (time.perf_counter() - t0) * 1000.0
            return json.loads(body), lat
    except Exception:
        return None, -1.0


# ── Chain probing ─────────────────────────────────────────────────────────────

def probe_all_chains():
    """
    Probe all chains in parallel.
    Returns list of result dicts, one per chain, sorted by chain id.
    """
    results = [None] * len(CHAINS)

    def _probe(idx, chain):
        resp, lat = wire_send_recv(
            chain["probe_host"], chain["probe_port"],
            {"type": "Probe"}
        )
        if resp is None:
            results[idx] = {
                **chain,
                "reachable":   False,
                "latencyMs":   -1.0,
                "queueLen":    -1,
                "waitTimeMs":  -1.0,
            }
        else:
            results[idx] = {
                **chain,
                "reachable":   True,
                "latencyMs":   round(lat, 2),
                "queueLen":    resp.get("queueLen", 0),
                "waitTimeMs":  resp.get("waitTimeMs", 0.0),
                "meanSvcMs":   resp.get("meanSvcMs", 0.0),
            }

    threads = [
        threading.Thread(target=_probe, args=(i, c), daemon=True)
        for i, c in enumerate(CHAINS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


# ── Selection policy ──────────────────────────────────────────────────────────

def select_random(probed):
    """Baseline 2: uniform random among reachable chains."""
    reachable = [c for c in probed if c["reachable"]]
    if not reachable:
        return None
    return random.choice(reachable)


# ── Request handler ───────────────────────────────────────────────────────────

_lock         = threading.Lock()
_request_count = 0


def handle_client(conn):
    global _request_count
    try:
        conn.settimeout(10.0)

        # Read request
        hdr = _recv_exact(conn, 4)
        if not hdr:
            return
        rlen = struct.unpack("!I", hdr)[0]
        body = _recv_exact(conn, rlen)
        if not body:
            return

        msg   = json.loads(body)
        t_in  = time.perf_counter()

        with _lock:
            _request_count += 1
            req_id = _request_count

        ue_id    = msg.get("ueId", "?")
        sst      = msg.get("sliceSst", 1)
        src_gnb  = msg.get("sourceGnb", "?")
        tgt_gnb  = msg.get("targetGnb", "?")

        # ── Print request header ──────────────────────────────────────────────
        print(f"\n{'━'*62}")
        print(f" Request #{req_id:>3d}  │  ue={ue_id}  sst={sst}  "
              f"src={src_gnb}  tgt={tgt_gnb}")
        print(f"{'━'*62}")
        print(f" Probing {len(CHAINS)} NF chains in parallel …\n")

        # ── Probe chains ──────────────────────────────────────────────────────
        probed = probe_all_chains()

        col1 = max(len(c["name"]) for c in probed) + 2
        for c in probed:
            if c["reachable"]:
                bar = "●" * min(c["queueLen"], 10)
                print(f"  ✓  {c['name']:<{col1}}"
                      f"RTT={c['latencyMs']:6.1f}ms  "
                      f"queue={c['queueLen']:2d} {bar:<10s}  "
                      f"wait={c['waitTimeMs']:5.1f}ms  "
                      f"svc≈{c.get('meanSvcMs', 0):5.1f}ms")
            else:
                print(f"  ✗  {c['name']:<{col1}}UNREACHABLE")

        # ── Select ────────────────────────────────────────────────────────────
        selected = select_random(probed)
        t_sel_ms = (time.perf_counter() - t_in) * 1000.0

        if selected is None:
            print("\n  [ERROR] All chains unreachable — cannot route handover")
            return

        print(f"\n  ➜  SELECTED  {selected['name']}")
        print(f"     AMF={selected['amf_addr']}  "
              f"probe={selected['latencyMs']:.1f}ms  "
              f"policy=random  "
              f"selection={t_sel_ms:.2f}ms")

        # ── Forward PathSwitchRequest to selected chain ───────────────────────
        # This is the actual core network execution: chain queues the request,
        # waits its turn, processes (AMF→SMF→UPF), and responds.
        # This is Metric 01: dispatcher receives → UPF completes path switch.
        chain_req = {
            "type":        "PathSwitchRequest",
            "ueId":        ue_id,
            "sliceSst":    sst,
            "sourceGnb":   src_gnb,
            "targetGnb":   tgt_gnb,
        }
        chain_resp, chain_lat = wire_send_recv(
            selected["probe_host"], selected["probe_port"],
            chain_req, timeout=30.0
        )

        t_total_ms = (time.perf_counter() - t_in) * 1000.0

        if chain_resp is None:
            print(f"  [ERROR] Chain {selected['id']} failed to process request")
            return

        wait_ms = chain_resp.get("waitMs", 0.0)
        svc_ms  = chain_resp.get("serviceMs", 0.0)

        print(f"     chain_wait={wait_ms:.1f}ms  "
              f"chain_svc={svc_ms:.1f}ms  "
              f"chain_rtt={chain_lat:.1f}ms")
        print(f"     total_core_latency={t_total_ms:.2f}ms")
        print(f"{'━'*62}")

        # ── Reply to gnb2 ─────────────────────────────────────────────────────
        resp = json.dumps({
            "type":             "PathSwitchResponse",
            "ueId":             ue_id,
            "selectedChain":    selected["id"],
            "selectedAmf":      selected["amf_addr"],
            "chainName":        selected["name"],
            "probeLatencyMs":   selected["latencyMs"],
            "queueLen":         selected["queueLen"],
            "selectionMs":      round(t_sel_ms, 2),
            "chainWaitMs":      round(wait_ms, 2),
            "chainServiceMs":   round(svc_ms, 2),
            "totalCoreMs":      round(t_total_ms, 2),
        }).encode()
        conn.sendall(struct.pack("!I", len(resp)) + resp)

    except Exception as e:
        print(f"[Dispatcher] handler error: {e}")
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(32)

    print("=" * 62)
    print("  NTN Handover Dispatcher  —  Baseline 2: Random Selection")
    print("=" * 62)
    print(f"  Listening  : {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"  NF chains  : {len(CHAINS)}")
    for c in CHAINS:
        print(f"    Chain {c['id']}: {c['name']}")
        print(f"             probe → {c['probe_host']}:{c['probe_port']}"
              f"   amf={c['amf_addr']}")
    print("=" * 62)
    print("  Waiting for PathSwitchRequest from gnb2 …\n")

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
