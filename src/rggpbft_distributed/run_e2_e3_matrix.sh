#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${1:-"$SCRIPT_DIR/results/e2-e3-$STAMP"}
mkdir -p "$OUT/e2" "$OUT/e3"
exec > >(tee "$OUT/formal.log") 2>&1

python3 -m unittest discover -s "$SCRIPT_DIR" -p 'test_*.py'
(cd "$SCRIPT_DIR" && docker build -f Dockerfile.v2 -t zte-rggpbft:v2 .)

for repeat in 1 2 3; do
    for nodes in 16 20 24 28 32; do
        for mode in pbft rgg; do
            run="$OUT/e2/${mode}-m${nodes}-r${repeat}"
            python3 "$SCRIPT_DIR/run_v2.py" --mode "$mode" --nodes "$nodes" --groups 4 \
                --rounds 20 --delay-ms 5 --round-timeout 15 --run-dir "$run" --skip-build
        done
    done
done

for repeat in 1 2 3; do
    for mode in pbft rgg; do
        python3 "$SCRIPT_DIR/run_v2.py" --mode "$mode" --nodes 16 --groups 4 --rounds 5 \
            --fault-mode crash --fault-nodes 15 --round-timeout 8 \
            --run-dir "$OUT/e3/${mode}-crash-replica-r${repeat}" --skip-build
        python3 "$SCRIPT_DIR/run_v2.py" --mode "$mode" --nodes 16 --groups 4 --rounds 5 \
            --fault-mode delay --fault-nodes 14,15 --fault-delay-ms 100 --round-timeout 15 \
            --run-dir "$OUT/e3/${mode}-delay-r${repeat}" --skip-build
        python3 "$SCRIPT_DIR/run_v2.py" --mode "$mode" --nodes 16 --groups 4 --rounds 5 \
            --fault-mode equivocation --fault-nodes 0 --round-timeout 8 \
            --run-dir "$OUT/e3/${mode}-equivocation-r${repeat}" --skip-build
        python3 "$SCRIPT_DIR/run_v2.py" --mode "$mode" --nodes 16 --groups 4 --rounds 3 \
            --fault-mode crash --fault-nodes 0 --round-timeout 5 \
            --run-dir "$OUT/e3/${mode}-crash-primary-r${repeat}" --skip-build
    done
done

python3 "$SCRIPT_DIR/aggregate_v2.py" "$OUT"
echo "E2/E3 matrix completed: $OUT"
