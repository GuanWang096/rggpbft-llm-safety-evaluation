#!/usr/bin/env bash
set -euo pipefail

export OMP_NUM_THREADS=8
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

SRC_ROOT="/root/src"
DATASET_ROOT="/root/datasets"
MODEL_DIR="/root/autodl-tmp/model/Qwen3-VL-8B-Instruct"
MODEL_REVISION="5d854aab08710c16b980ec6d603d863b3821b915"
RESULT_ROOT="/root/result/multijudge-v15"
RUNNER="${SRC_ROOT}/src/multijudge_workflows/run_judge.py"
COMPARATOR="${SRC_ROOT}/src/multijudge_workflows/compare_qwen_input_modes.py"
MANIFEST="${SRC_ROOT}/src/multijudge_workflows/configs/mmds_smoke_100.json"
POLICY="${SRC_ROOT}/src/multijudge_workflows/configs/canonical_policy_v1.json"

python -m pip install -r "${SRC_ROOT}/requirements-gpu.txt"

if [[ -d "${SRC_ROOT}/src/multijudge" ]]; then
  python -m pip install --no-deps -e "${SRC_ROOT}"
else
  echo "Cannot find multijudge at ${SRC_ROOT}/src/multijudge." >&2
  exit 1
fi

python "${SRC_ROOT}/src/multijudge_workflows/model_preflight.py" \
  --model-path "${MODEL_DIR}" \
  --revision "${MODEL_REVISION}" \
  --expected-architecture Qwen3VLForConditionalGeneration \
  --dataset-root "${DATASET_ROOT}" \
  --require-cuda

mkdir -p "${RESULT_ROOT}"

COMMON_ARGS=(
  --dataset-root "${DATASET_ROOT}"
  --manifest "${MANIFEST}"
  --policy "${POLICY}"
  --model-path "${MODEL_DIR}"
  --model-revision "${MODEL_REVISION}"
  --max-new-tokens 96
  --attn-implementation sdpa
  --max-parser-failure-rate 0
)

python -u "${RUNNER}" \
  "${COMMON_ARGS[@]}" \
  --input-mode native \
  --limit 3 \
  --run-dir "${RESULT_ROOT}/qwen-native-qual3"

python -u "${RUNNER}" \
  "${COMMON_ARGS[@]}" \
  --input-mode panel \
  --limit 3 \
  --run-dir "${RESULT_ROOT}/qwen-panel-qual3"

python -u "${RUNNER}" \
  "${COMMON_ARGS[@]}" \
  --input-mode native \
  --run-dir "${RESULT_ROOT}/qwen-native-100"

python -u "${RUNNER}" \
  "${COMMON_ARGS[@]}" \
  --input-mode panel \
  --run-dir "${RESULT_ROOT}/qwen-panel-100"

python -u "${COMPARATOR}" \
  --native "${RESULT_ROOT}/qwen-native-100/judgments.jsonl" \
  --panel "${RESULT_ROOT}/qwen-panel-100/judgments.jsonl" \
  --output "${RESULT_ROOT}/qwen-input-fidelity.json"

echo "Qwen3-VL smoke and input-fidelity gate completed."
