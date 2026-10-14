#!/bin/bash
# Start all 3 NF chain simulators and the dispatcher.
# Run this BEFORE starting gnb2 so chains are ready.
#
# Chain load profiles:
#   Chain 1: low-load   μ=200 req/s  →  mean service  5ms
#   Chain 2: med-load   μ=100 req/s  →  mean service 10ms
#   Chain 3: high-load  μ= 50 req/s  →  mean service 20ms

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Stopping any previous instances..."
pkill -f "nf_chain.py"   2>/dev/null || true
pkill -f "dispatcher.py" 2>/dev/null || true
sleep 0.3

echo ""
echo "Starting NF chain simulators..."
python3 "$DIR/nf_chain.py" --name "Chain-1-low"  --port 9101 --mu 200 &
PID1=$!
python3 "$DIR/nf_chain.py" --name "Chain-2-med"  --port 9102 --mu 100 &
PID2=$!
python3 "$DIR/nf_chain.py" --name "Chain-3-high" --port 9103 --mu 50  &
PID3=$!

sleep 0.5
echo "  Chain 1 (low-load)   → 127.0.0.1:9101  [PID $PID1]"
echo "  Chain 2 (med-load)   → 127.0.0.1:9102  [PID $PID2]"
echo "  Chain 3 (high-load)  → 127.0.0.1:9103  [PID $PID3]"
echo ""
echo "Starting dispatcher on 127.0.0.1:9000 ..."
echo "(Ctrl+C to stop everything)"
echo ""

# Run dispatcher in foreground so logs appear in this terminal
python3 "$DIR/dispatcher.py"
