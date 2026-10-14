#!/usr/bin/env python3
"""
NF Chain Simulator — represents one AMF→SMF→UPF chain.

Models an M/M/1 queue:  arrivals join the queue, wait their turn,
then receive exponentially-distributed service from the NF chain.
Responds to two message types:
  - {"type":"Probe"}                → fast reply with current queue state
  - {"type":"PathSwitchRequest",...} → full processing with queue delay

Usage:
    python3 nf_chain.py --name Chain-1 --port 9101 --mu 200
    python3 nf_chain.py --name Chain-2 --port 9102 --mu 100
    python3 nf_chain.py --name Chain-3 --port 9103 --mu 50
"""

import argparse
import json
import random
import socket
import struct
import threading
import time


# ── Wire helpers ──────────────────────────────────────────────────────────────

def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_msg(s):
    hdr = _recv_exact(s, 4)
    if not hdr:
        return None
    length = struct.unpack("!I", hdr)[0]
    if length == 0 or length > 1_048_576:
        return None
    raw = _recv_exact(s, length)
    return json.loads(raw) if raw else None


def send_msg(s, obj):
    raw = json.dumps(obj).encode()
    s.sendall(struct.pack("!I", len(raw)) + raw)


# ── M/M/1 Queue ───────────────────────────────────────────────────────────────

class MM1Queue:
    """
    Simulates M/M/1 queue backlog.
    mu = service rate in requests/sec  →  mean service time = 1000/mu ms
    """

    def __init__(self, mu: float):
        self.mu = mu
        self._lock = threading.Lock()
        self._q = 0          # current number of requests in system

    def enter(self):
        with self._lock:
            self._q += 1

    def leave(self):
        with self._lock:
            self._q = max(0, self._q - 1)

    @property
    def queue_len(self):
        with self._lock:
            return self._q

    def sample_service_ms(self):
        return random.expovariate(self.mu) * 1000.0

    def expected_wait_ms(self):
        # W = Q / mu  (Little's law approximation)
        return self.queue_len / self.mu * 1000.0


# ── Connection handler ────────────────────────────────────────────────────────

def handle(conn, name, queue, log):
    try:
        conn.settimeout(10.0)
        msg = recv_msg(conn)
        if msg is None:
            return

        msg_type = msg.get("type", "")

        if msg_type == "Probe":
            send_msg(conn, {
                "type":        "ProbeAck",
                "chain":       name,
                "queueLen":    queue.queue_len,
                "waitTimeMs":  round(queue.expected_wait_ms(), 2),
                "serviceRate": queue.mu,
                "meanSvcMs":   round(1000.0 / queue.mu, 1),
            })
            return

        # Full request: enqueue → wait → service → dequeue
        queue.enter()
        wait_ms = queue.expected_wait_ms()
        svc_ms  = queue.sample_service_ms()
        time.sleep((wait_ms + svc_ms) / 1000.0)
        queue.leave()

        ue_id = msg.get("ueId", "?")
        log(f"[{name}] processed | ue={ue_id} "
            f"queue={queue.queue_len} wait={wait_ms:.1f}ms svc={svc_ms:.1f}ms")

        send_msg(conn, {
            "type":      "PathSwitchAck",
            "chain":     name,
            "ueId":      ue_id,
            "queueLen":  queue.queue_len,
            "waitMs":    round(wait_ms, 2),
            "serviceMs": round(svc_ms, 2),
        })

    except Exception as e:
        log(f"[{name}] error: {e}")
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="NF Chain Simulator (AMF→SMF→UPF)")
    ap.add_argument("--name", default="Chain-1",     help="Chain display name")
    ap.add_argument("--host", default="127.0.0.1",   help="Bind address")
    ap.add_argument("--port", type=int, required=True, help="Listen port")
    ap.add_argument("--mu",   type=float, default=100.0,
                    help="Service rate in req/s (default 100 → mean 10ms)")
    args = ap.parse_args()

    queue = MM1Queue(mu=args.mu)
    log   = lambda s: print(s, flush=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(64)

    log(f"[{args.name}] listening on {args.host}:{args.port} | "
        f"mu={args.mu:.0f} req/s  mean_svc={1000/args.mu:.1f}ms")

    while True:
        conn, _ = srv.accept()
        threading.Thread(
            target=handle, args=(conn, args.name, queue, log), daemon=True
        ).start()


if __name__ == "__main__":
    main()
