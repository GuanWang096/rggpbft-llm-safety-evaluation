#!/usr/bin/env bash
set -u

RUN_DIR=/root/result/full-qwen3vl4b-512
LOG_FILE="$RUN_DIR/pipeline.log"
PID_FILE="$RUN_DIR/pipeline.pid"
STATUS_FILE="$RUN_DIR/pipeline_status.json"
TOTAL=2062
SCRIPT_PATH="$(readlink -f "$0")"

run_pipeline() {
    set -euo pipefail
    export OMP_NUM_THREADS=8
    cd /root/src

    python -u run_e1.py generate \
        --run-dir "$RUN_DIR" \
        --dataset both \
        --dataset-root /root/datasets \
        --model-path /root/autodl-tmp/model/Qwen3-VL-4B-Instruct \
        --seed 20260704 \
        --max-new-tokens 512

    python -u run_e1.py moderate \
        --run-dir "$RUN_DIR" \
        --model-path /root/autodl-tmp/model/Qwen3Guard-Gen-4B \
        --max-new-tokens 64

    python run_e1.py summarize --run-dir "$RUN_DIR"

    cd "$RUN_DIR"
    sha256sum -c checksums.sha256
}

if [[ "${1:-}" == "--worker" ]]; then
    printf '{"state":"running"}\n' >"$STATUS_FILE"
    set +e
    ( run_pipeline )
    EXIT_STATUS=$?
    set -e
    if (( EXIT_STATUS == 0 )); then
        printf '{"state":"completed","exit_status":0}\n' >"$STATUS_FILE"
        exit 0
    else
        printf '{"state":"failed","exit_status":%d}\n' "$EXIT_STATUS" >"$STATUS_FILE"
        exit "$EXIT_STATUS"
    fi
fi

mkdir -p "$RUN_DIR"

PID=""
if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
fi

if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    echo "Existing pipeline detected: PID=$PID"
else
    nohup "$SCRIPT_PATH" --worker >"$LOG_FILE" 2>&1 &
    PID=$!
    echo "$PID" >"$PID_FILE"
    echo "Pipeline started: PID=$PID"
fi

LAST_STAGE=""
STAGE_START=$(date +%s)

while kill -0 "$PID" 2>/dev/null; do
    if [[ -f "$RUN_DIR/moderation.jsonl" ]]; then
        STAGE="Guard"
        DONE=$(wc -l <"$RUN_DIR/moderation.jsonl")
    else
        STAGE="VLM"
        if [[ -f "$RUN_DIR/generation.jsonl" ]]; then
            DONE=$(wc -l <"$RUN_DIR/generation.jsonl")
        else
            DONE=0
        fi
    fi

    if [[ "$STAGE" != "$LAST_STAGE" ]]; then
        if [[ "$STAGE" == "Guard" ]]; then
            STATUS_FILE="$RUN_DIR/moderation_status.json"
        else
            STATUS_FILE="$RUN_DIR/generation_status.json"
        fi
        if [[ -f "$STATUS_FILE" ]]; then
            STAGE_START=$(python -c \
                'import json,sys; print(json.load(open(sys.argv[1]))["started_at_unix"])' \
                "$STATUS_FILE" 2>/dev/null || date +%s)
        else
            STAGE_START=$(date +%s)
        fi
        LAST_STAGE="$STAGE"
    fi

    if (( DONE > TOTAL )); then
        DONE=$TOTAL
    fi
    PERCENT=$((DONE * 100 / TOTAL))
    FILLED=$((PERCENT * 40 / 100))
    EMPTY=$((40 - FILLED))
    BAR_FILLED=$(printf "%*s" "$FILLED" "" | tr " " "#")
    BAR_EMPTY=$(printf "%*s" "$EMPTY" "" | tr " " "-")

    NOW=$(date +%s)
    ELAPSED=$((NOW - STAGE_START))
    if (( DONE > 0 )); then
        ETA_MIN=$(((TOTAL - DONE) * ELAPSED / DONE / 60))
    else
        ETA_MIN=0
    fi

    printf "\r%-6s [%s%s] %3d%%  %d/%d  ETA %d min" \
        "$STAGE" "$BAR_FILLED" "$BAR_EMPTY" \
        "$PERCENT" "$DONE" "$TOTAL" "$ETA_MIN"
    sleep 10
done

echo
echo "Pipeline process ended."

PIPELINE_STATE=$(python -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
    "$STATUS_FILE" 2>/dev/null || true)

if [[ "$PIPELINE_STATE" == "completed" && -f "$RUN_DIR/summary.json" ]]; then
    echo "Pipeline completed successfully."
    cat "$RUN_DIR/summary.json"
    echo
    cd "$RUN_DIR"
    sha256sum -c checksums.sha256
else
    echo "Pipeline failed or did not pass the quality gate. Last 50 log lines:"
    tail -n 50 "$LOG_FILE"
    exit 1
fi
