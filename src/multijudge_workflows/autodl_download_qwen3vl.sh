#!/usr/bin/env bash
set -euo pipefail

MODEL_ID="Qwen/Qwen3-VL-8B-Instruct"
MODEL_REVISION="5d854aab08710c16b980ec6d603d863b3821b915"
MODEL_DIR="/root/autodl-tmp/model/Qwen3-VL-8B-Instruct"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python -m pip install --upgrade "modelscope-hub==0.1.6"
mkdir -p "${MODEL_DIR}"

modelscope download "${MODEL_ID}" \
  --revision "${MODEL_REVISION}" \
  --local-dir "${MODEL_DIR}" \
  --max-workers 8

python "${SCRIPT_DIR}/model_preflight.py" \
  --model-path "${MODEL_DIR}" \
  --revision "${MODEL_REVISION}" \
  --expected-architecture Qwen3VLForConditionalGeneration
