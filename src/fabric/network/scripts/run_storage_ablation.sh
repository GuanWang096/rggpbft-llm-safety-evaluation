#!/usr/bin/env bash
set -euo pipefail
export GOFLAGS="${GOFLAGS:-} -buildvcs=false"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${1:-"$FABRIC_ROOT/results/e5-storage-$STAMP"}
BIN="$OUT/storage-workload"
mkdir -p "$OUT"
exec > >(tee "$OUT/formal.log") 2>&1

bash "$SCRIPT_DIR/upgrade_chaincode.sh" 1.2 3
(cd "$FABRIC_ROOT/client" && go test -race ./... && go build -o "$BIN" ./cmd/workload)

for repeat in 1 2 3; do
    for payload in 1024 65536 1048576; do
        for concurrency in 1 4; do
            for storage in hybrid inline; do
                "$BIN" -storage "$storage" -c "$concurrency" -s "$payload" -n 20 -warmup 2 \
                    -seed "$((20260704 + repeat))" -out "$OUT/runs"
            done
        done
    done
done

python3 "$FABRIC_ROOT/benchmarks/aggregate_storage.py" "$OUT"
echo "Storage ablation completed: $OUT"
