#!/bin/sh
# Apply netem qdisc to the egress interface used for peer communication.
# Usage: netem_apply.sh <interface> <delay_ms> <jitter_ms> <loss_pct>
set -e

IFACE="${1:-eth0}"
DELAY="${2:-0}"
JITTER="${3:-0}"
LOSS="${4:-0}"

# Clear any existing qdisc
tc qdisc del dev "$IFACE" root 2>/dev/null || true

# Build netem command
if [ "$DELAY" = "0" ] && [ "$JITTER" = "0" ] && [ "$LOSS" = "0" ]; then
    echo "No netem parameters, leaving default qdisc on $IFACE"
    exit 0
fi

CMD="tc qdisc add dev $IFACE root netem"
if [ "$DELAY" != "0" ]; then
    if [ "$JITTER" != "0" ]; then
        CMD="$CMD delay ${DELAY}ms ${JITTER}ms"
    else
        CMD="$CMD delay ${DELAY}ms"
    fi
fi
# Integer comparison without bc dependency
_LOSS_INT=$(printf "%.0f" "$LOSS" 2>/dev/null || echo 0)
if [ "$_LOSS_INT" -gt 0 ] 2>/dev/null; then
    CMD="$CMD loss $LOSS%"
fi

echo "Applying: $CMD"
$CMD
echo "Applied. Current qdisc:"
tc qdisc show dev "$IFACE"
