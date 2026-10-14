#!/bin/bash
# ── NTN propagation delay injection ──────────────────────────────────────────
#
# Injects one-way propagation delay onto the individual 5G interfaces so that
# NTN link geometry can be reproduced on the loopback testbed.
#
# LINKS
#   radio   UE <-> gNB      (Uu)   carried on the veth pair into the ue1 netns
#   xn      gNB <-> gNB     (Xn)   TCP  38422 on lo
#   n2      gNB <-> AMF     (N2)   SCTP 38412 on lo
#   n3      gNB <-> UPF     (N3)   UDP  2152  on lo
#   feeder  shorthand for n2 + n3 together
#
# ARCHITECTURE MAPPING
#   transparent   satellite is a bent-pipe relay, gNB on the ground
#                 -> the service+feeder hop is the RADIO link
#   regenerative  gNB on board the satellite
#                 -> radio = service link, FEEDER (n2+n3) crosses to the ground
#   isl           source and target gNB on different satellites
#                 -> the XN link is the inter-satellite hop
#
# netem shapes EGRESS only, so each link is delayed at both ends and each side
# carries the ONE-WAY delay; the round trip then comes out at 2x naturally.
#
# Usage:
#   sudo ./netem.sh init                 prepare qdiscs (idempotent)
#   sudo ./netem.sh set radio 5.985      one-way ms on a link
#   sudo ./netem.sh set feeder 3.275
#   sudo ./netem.sh clear                remove all shaping
#   sudo ./netem.sh show                 current state
#   sudo ./netem.sh verify               measure the resulting RTTs

set -euo pipefail

NS=ue1
VETH_HOST=veth-ue1
VETH_NS=veth-ue1-ns

# lo band assignment: 1:1 is the undelayed default, one band per interface.
BAND_XN=1:2
BAND_N2=1:3
BAND_N3=1:4
HANDLE_XN=20
HANDLE_N2=30
HANDLE_N3=40

PORT_XN=38422    # TCP
PORT_N2=38412    # SCTP
PORT_N3=2152     # UDP

PROTO_TCP=6
PROTO_UDP=17
PROTO_SCTP=132

# Per-gNB radio addresses on the veth. Each gNB gets its own band on BOTH ends
# of the veth pair, so the serving and candidate satellites can carry different
# service-link delays -- which is the whole premise of an elevation-triggered
# handover: the serving satellite is setting (far) while the next is rising (near).
GNB_ADDRS=(10.200.0.11 10.200.0.12 10.200.0.13)

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo $0 $*" >&2
    exit 1
fi

lo_filter() {   # proto port band prio
    local proto=$1 port=$2 band=$3 prio=$4
    # Both directions: the request carries the port as destination, the reply
    # carries it as source.
    tc filter add dev lo protocol ip parent 1:0 prio "$prio" u32 \
        match ip protocol "$proto" 0xff match ip dport "$port" 0xffff flowid "$band"
    tc filter add dev lo protocol ip parent 1:0 prio "$prio" u32 \
        match ip protocol "$proto" 0xff match ip sport "$port" 0xffff flowid "$band"
}

do_init() {
    tc qdisc del dev lo root 2>/dev/null || true

    # priomap all-zero: everything unclassified lands in band 0 (1:1), undelayed.
    tc qdisc add dev lo root handle 1: prio bands 4 \
        priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0

    tc qdisc add dev lo parent $BAND_XN handle ${HANDLE_XN}: netem delay 0ms
    tc qdisc add dev lo parent $BAND_N2 handle ${HANDLE_N2}: netem delay 0ms
    tc qdisc add dev lo parent $BAND_N3 handle ${HANDLE_N3}: netem delay 0ms

    lo_filter $PROTO_TCP  $PORT_XN $BAND_XN 1
    lo_filter $PROTO_SCTP $PORT_N2 $BAND_N2 2
    lo_filter $PROTO_UDP  $PORT_N3 $BAND_N3 3

    # Radio: one band per gNB on each end of the veth pair.
    #   host side  -> egress toward the UE, so classify on SOURCE (the gNB)
    #   netns side -> egress toward the gNBs, so classify on DESTINATION
    tc qdisc del dev $VETH_HOST root 2>/dev/null || true
    ip netns exec $NS tc qdisc del dev $VETH_NS root 2>/dev/null || true

    tc qdisc add dev $VETH_HOST root handle 1: prio bands 4 \
        priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
    ip netns exec $NS tc qdisc add dev $VETH_NS root handle 1: prio bands 4 \
        priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0

    local i band
    for i in 0 1 2; do
        band="1:$((i + 2))"
        tc qdisc add dev $VETH_HOST parent "$band" handle "$((i + 12)):" netem delay 0ms
        ip netns exec $NS tc qdisc add dev $VETH_NS parent "$band" \
            handle "$((i + 12)):" netem delay 0ms

        tc filter add dev $VETH_HOST protocol ip parent 1:0 prio $((i + 1)) u32 \
            match ip src "${GNB_ADDRS[$i]}" flowid "$band"
        ip netns exec $NS tc filter add dev $VETH_NS protocol ip parent 1:0 \
            prio $((i + 1)) u32 match ip dst "${GNB_ADDRS[$i]}" flowid "$band"
    done

    echo "netem initialised (all links at 0 ms, per-gNB radio bands ready)"
}

ensure_init() {
    if ! tc qdisc show dev lo | grep -q "prio 1:" \
       || ! tc qdisc show dev $VETH_HOST | grep -q "prio 1:"; then
        do_init >/dev/null
    fi
}

set_radio_gnb() {  # gnb_index(1..3) delay_ms
    local idx=$(( $1 - 1 )) band handle
    band="1:$((idx + 2))"
    handle="$((idx + 12)):"
    tc qdisc change dev $VETH_HOST parent "$band" handle "$handle" netem delay "${2}ms"
    ip netns exec $NS tc qdisc change dev $VETH_NS parent "$band" \
        handle "$handle" netem delay "${2}ms"
}

set_lo_band() {  # handle band delay_ms
    tc qdisc change dev lo parent "$2" handle "${1}:" netem delay "${3}ms"
}

do_set() {
    local link=$1 delay=$2
    ensure_init

    case "$link" in
        radio)
            # All gNBs together: the E2 sweep case, where both endpoints stand
            # for the same geometry.
            set_radio_gnb 1 "$delay"
            set_radio_gnb 2 "$delay"
            set_radio_gnb 3 "$delay"
            ;;
        gnb1)   set_radio_gnb 1 "$delay" ;;
        gnb2)   set_radio_gnb 2 "$delay" ;;
        gnb3)   set_radio_gnb 3 "$delay" ;;
        xn)     set_lo_band $HANDLE_XN $BAND_XN "$delay" ;;
        n2)     set_lo_band $HANDLE_N2 $BAND_N2 "$delay" ;;
        n3)     set_lo_band $HANDLE_N3 $BAND_N3 "$delay" ;;
        feeder)
            set_lo_band $HANDLE_N2 $BAND_N2 "$delay"
            set_lo_band $HANDLE_N3 $BAND_N3 "$delay"
            ;;
        all)
            do_set radio "$delay"
            do_set xn "$delay"
            do_set feeder "$delay"
            ;;
        *)
            echo "Unknown link '$link' (radio|xn|n2|n3|feeder|all)" >&2
            exit 1
            ;;
    esac
    local rtt
    rtt=$(awk -v d="$delay" 'BEGIN { printf "%.3f", 2 * d }')
    echo "$link = ${delay} ms one-way (RTT ${rtt} ms)"
}

do_clear() {
    tc qdisc del dev lo root 2>/dev/null || true
    tc qdisc del dev $VETH_HOST root 2>/dev/null || true
    ip netns exec $NS tc qdisc del dev $VETH_NS root 2>/dev/null || true
    echo "netem cleared"
}

do_show() {
    echo "── lo ──"; tc qdisc show dev lo
    echo "── lo filters ──"; tc filter show dev lo parent 1:0 | grep -E "flowid|match" | head -20
    echo "── $VETH_HOST (per-gNB radio bands) ──"; tc qdisc show dev $VETH_HOST
    echo "── $VETH_NS (in $NS) ──"; ip netns exec $NS tc qdisc show dev $VETH_NS
}

do_verify() {
    echo "Measuring effective RTTs (3 pings each)"
    echo
    echo -n "radio+n3 (UE -> UPF, through GTP):  "
    ip netns exec $NS ping -I uesimtun0 -c 3 -q 10.45.0.1 2>/dev/null \
        | awk -F'/' '/rtt|round-trip/ {printf "avg %.3f ms\n", $5}' || echo "FAILED"
    echo -n "veth only (UE netns -> host):       "
    ip netns exec $NS ping -c 3 -q 10.200.0.11 2>/dev/null \
        | awk -F'/' '/rtt|round-trip/ {printf "avg %.3f ms\n", $5}' || echo "FAILED"
    echo
    echo "Note: the first figure includes the radio link twice plus N3 twice,"
    echo "      so it rises with both 'radio' and 'n3'/'feeder' settings."
}

case "${1:-}" in
    init)   do_init ;;
    set)    [[ $# -eq 3 ]] || { echo "usage: $0 set <link> <one-way-ms>" >&2; exit 1; }
            do_set "$2" "$3" ;;
    clear)  do_clear ;;
    show)   do_show ;;
    verify) do_verify ;;
    *)
        echo "usage: $0 {init|set <link> <ms>|clear|show|verify}" >&2
        echo "  links: radio xn n2 n3 feeder all" >&2
        echo "  per-gNB radio (for the orchestrator): gnb1 gnb2 gnb3" >&2
        exit 1
        ;;
esac
