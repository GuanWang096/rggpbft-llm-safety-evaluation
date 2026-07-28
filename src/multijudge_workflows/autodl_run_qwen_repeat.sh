#!/usr/bin/env bash
set -euo pipefail

export OMP_NUM_THREADS=8
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PYTHONPATH="/root/src/src${PYTHONPATH:+:${PYTHONPATH}}"

SRC_ROOT="/root/src"
MODEL_PATH="/root/autodl-tmp/model/Qwen3-VL-8B-Instruct"
RESULT_ROOT="/root/result/multijudge-v15"
AUDIT_DIR="${RESULT_ROOT}/qwen-audit"

cd "$SRC_ROOT"
python -m pip install -r requirements-gpu.txt

python -u src/multijudge_workflows/capture_runtime.py \
  --model-path "$MODEL_PATH" \
  --output-dir "$AUDIT_DIR"

python -u src/multijudge_workflows/run_judge.py \
  --dataset-root /root/datasets \
  --manifest src/multijudge_workflows/configs/mmds_smoke_100.json \
  --policy src/multijudge_workflows/configs/canonical_policy_v1.json \
  --model-path "$MODEL_PATH" \
  --model-revision 5d854aab08710c16b980ec6d603d863b3821b915 \
  --input-mode native \
  --limit 3 \
  --max-new-tokens 96 \
  --attn-implementation sdpa \
  --max-parser-failure-rate 0 \
  --run-dir "${RESULT_ROOT}/qwen-native-repeat3"

python -u src/multijudge_workflows/compare_repeatability.py \
  --first "${RESULT_ROOT}/qwen-native-qual3/judgments.jsonl" \
  --second "${RESULT_ROOT}/qwen-native-repeat3/judgments.jsonl" \
  --output "${RESULT_ROOT}/qwen-native-repeatability.json"

echo "Qwen repeatability check completed successfully."
