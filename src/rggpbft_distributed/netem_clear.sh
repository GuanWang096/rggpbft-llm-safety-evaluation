#!/bin/sh
# Clear netem qdisc from interface.
IFACE="${1:-eth0}"
tc qdisc del dev "$IFACE" root 2>/dev/null && echo "Qdisc cleared on $IFACE" || echo "No qdisc to clear on $IFACE"
