#!/usr/bin/env bash
set -euo pipefail
export GOFLAGS="${GOFLAGS:-} -buildvcs=false"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
BASELINE="$FABRIC_ROOT/baseline"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${1:-"$FABRIC_ROOT/results/e6-formal-$STAMP"}
BIN="$OUT/e6-workload"
mkdir -p "$OUT/ingress" "$OUT/lifecycle"
exec > >(tee "$OUT/formal.log") 2>&1

(cd "$BASELINE" && go test -race ./... && go build -o "$BIN" ./cmd/workload)

for repeat in 1 2 3; do
    for payload in 1024 65536 1048576; do
        for concurrency in 1 4 8 16; do
            "$BIN" -mode ingress -c "$concurrency" -s "$payload" -n 30 -warmup 3 \
                -seed "$((20260704 + repeat))" -out "$OUT/ingress"
        done
    done
done

for repeat in 1 2 3; do
    for concurrency in 1 4 8; do
        "$BIN" -mode lifecycle -c "$concurrency" -s 65536 -n 12 -warmup 1 \
            -seed "$((20260704 + repeat))" -out "$OUT/lifecycle"
    done
done

python3 "$BASELINE/aggregate_e6.py" "$OUT"
echo "E6 formal matrix completed: $OUT"
