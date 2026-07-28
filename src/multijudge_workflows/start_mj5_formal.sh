#!/usr/bin/env bash
set -euo pipefail

SESSION_ID="${1:-mj5-formal-20260726-final}"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
EXPERIMENT_DIR="${REPO_ROOT}/src/multijudge_workflows"
SESSION_DIR="${REPO_ROOT}/results/cross_layer/runs/${SESSION_ID}"
PID_FILE="${SESSION_DIR}/pipeline.pid"
LOG_FILE="${SESSION_DIR}/pipeline.log"

mkdir -p "${SESSION_DIR}"

if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    echo "MJ5 formal run is already active: PID=$(cat "${PID_FILE}")"
    exit 0
fi

cd "${EXPERIMENT_DIR}"
nohup setsid python3 -u run_mj5_formal.py \
    --session-id "${SESSION_ID}" \
    >"${LOG_FILE}" 2>&1 </dev/null &
PID=$!
echo "${PID}" >"${PID_FILE}"
sleep 2

if ! kill -0 "${PID}" 2>/dev/null; then
    echo "MJ5 formal run failed to start."
    tail -n 40 "${LOG_FILE}" || true
    exit 1
fi

echo "MJ5 formal run started: PID=${PID}"
echo "Log: ${LOG_FILE}"
