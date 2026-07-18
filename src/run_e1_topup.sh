#!/usr/bin/env bash
set -u

BASE_RUN=/root/result/full-qwen3vl4b-512
RUN_DIR=/root/result/full-qwen3vl4b-2048-topup
LOG_FILE="$RUN_DIR/pipeline.log"
PID_FILE="$RUN_DIR/pipeline.pid"
STATUS_FILE="$RUN_DIR/pipeline_status.json"
SCRIPT_PATH="$(readlink -f "$0")"

run_pipeline() {
    set -euo pipefail
    export OMP_NUM_THREADS=8
    cd /root/src

    python -u run_e1_topup.py \
        --base-run "$BASE_RUN" \
        --run-dir "$RUN_DIR" \
        --dataset-root /root/datasets \
        --model-path /root/autodl-tmp/model/Qwen3-VL-4B-Instruct \
        --guard-model-path /root/autodl-tmp/model/Qwen3Guard-Gen-4B \
        --base-limit 512 \
        --max-new-tokens 2048 \
        --guard-max-new-tokens 64 \
        --max-limit-hit-rate 0.01
}

if [[ "${1:-}" == "--worker" ]]; then
    mkdir -p "$RUN_DIR"
    printf '{"state":"running"}\n' >"$STATUS_FILE"
    set +e
    ( run_pipeline )
    EXIT_STATUS=$?
    set -e
    if (( EXIT_STATUS == 0 )); then
        printf '{"state":"completed","exit_status":0}\n' >"$STATUS_FILE"
    else
        printf '{"state":"failed","exit_status":%d}\n' "$EXIT_STATUS" >"$STATUS_FILE"
    fi
    exit "$EXIT_STATUS"
fi

mkdir -p "$RUN_DIR"

PID=""
if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
fi

if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "Existing top-up pipeline detected: PID=$PID"
else
    nohup "$SCRIPT_PATH" --worker >"$LOG_FILE" 2>&1 &
    PID=$!
    echo "$PID" >"$PID_FILE"
    echo "Top-up pipeline started: PID=$PID"
fi

while kill -0 "$PID" 2>/dev/null; do
    if [[ -f "$RUN_DIR/moderation.jsonl" ]]; then
        STAGE="Guard"
        DONE=$(wc -l <"$RUN_DIR/moderation.jsonl")
        TOTAL=2062
    else
        STAGE="VLM-topup"
        if [[ -f "$RUN_DIR/generation_topup.jsonl" ]]; then
            DONE=$(wc -l <"$RUN_DIR/generation_topup.jsonl")
        else
            DONE=0
        fi
        if [[ -f "$RUN_DIR/topup_config.json" ]]; then
            TOTAL=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["selected_count"])' "$RUN_DIR/topup_config.json")
        else
            TOTAL=447
        fi
    fi

    if (( DONE > TOTAL )); then DONE=$TOTAL; fi
    PERCENT=$((DONE * 100 / TOTAL))
    FILLED=$((PERCENT * 40 / 100))
    EMPTY=$((40 - FILLED))
    BAR_FILLED=$(printf "%*s" "$FILLED" "" | tr " " "#")
    BAR_EMPTY=$(printf "%*s" "$EMPTY" "" | tr " " "-")
    printf "\r%-9s [%s%s] %3d%%  %d/%d" \
        "$STAGE" "$BAR_FILLED" "$BAR_EMPTY" "$PERCENT" "$DONE" "$TOTAL"
    sleep 10
done

echo
PIPELINE_STATE=$(python -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
    "$STATUS_FILE" 2>/dev/null || true)

if [[ "$PIPELINE_STATE" == "completed" && -f "$RUN_DIR/summary.json" ]]; then
    echo "Top-up pipeline completed successfully."
    cat "$RUN_DIR/summary.json"
else
    echo "Top-up pipeline failed or did not pass the quality gate."
    tail -n 50 "$LOG_FILE"
    exit 1
fi
