#!/usr/bin/env bash
set -euo pipefail
export GOFLAGS="${GOFLAGS:-} -buildvcs=false"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${1:-"$FABRIC_ROOT/results/e4-formal-$STAMP"}
INGRESS_OUT="$OUT/ingress"
LIFECYCLE_OUT="$OUT/lifecycle"
CLIENT="$FABRIC_ROOT/client"
BENCHMARKS="$FABRIC_ROOT/benchmarks"
BIN="$OUT/e4-ingress"

mkdir -p "$INGRESS_OUT" "$LIFECYCLE_OUT"
exec > >(tee "$OUT/formal.log") 2>&1

echo "Building race-tested ingress benchmark"
(cd "$CLIENT" && go test -race ./... && go build -o "$BIN" ./cmd/workload)
python3 -m unittest discover -s "$BENCHMARKS" -p 'test_*.py'

for repeat in 1 2 3; do
    for payload in 1024 65536 1048576; do
        for concurrency in 1 4 8 16; do
            echo "Ingress repeat=$repeat payload=$payload concurrency=$concurrency"
            "$BIN" -c "$concurrency" -s "$payload" -n 30 -warmup 3 \
                -seed "$((20260704 + repeat))" -out "$INGRESS_OUT"
        done
    done
done

for repeat in 1 2 3; do
    for concurrency in 1 4 8; do
        echo "Lifecycle repeat=$repeat payload=65536 concurrency=$concurrency"
        python3 "$BENCHMARKS/e4_full_lifecycle.py" \
            --fabric-root "$FABRIC_ROOT" --output "$LIFECYCLE_OUT" \
            --concurrency "$concurrency" --payload-size 65536 \
            --tasks 12 --warmup 1 --seed "$((20260704 + repeat))"
    done
done

python3 "$BENCHMARKS/aggregate_e4.py" "$OUT"
echo "E4 formal matrix completed: $OUT"
