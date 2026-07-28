#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 safework|internvl|minicpm" >&2
  exit 2
fi

export OMP_NUM_THREADS=8
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PYTHONPATH="/root/src/src${PYTHONPATH:+:${PYTHONPATH}}"

SRC_ROOT="/root/src"
DATASET_ROOT="/root/datasets"
RESULT_ROOT="/root/result/multijudge-v15/native-judges"
MANIFEST="${SRC_ROOT}/src/multijudge_workflows/configs/mmds_smoke_100.json"
POLICY="${SRC_ROOT}/src/multijudge_workflows/configs/canonical_policy_v1.json"

case "$1" in
  safework)
    ADAPTER="safework"
    MODEL_PATH="/root/autodl-tmp/model/SafeWork-RM-Safety-7B"
    REVISION="be345f29425fe94586c0598785a143703bbbc4fc"
    ;;
  internvl)
    ADAPTER="internvl"
    MODEL_PATH="/root/autodl-tmp/model/InternVL3_5-8B-Instruct"
    REVISION="6c2034f6f3d22bbbff919b11b91c5721bba84f8d"
    ;;
  minicpm)
    ADAPTER="minicpm"
    MODEL_PATH="/root/autodl-tmp/model/MiniCPM-V-4_5"
    REVISION="2626e837a54905aab70fae9325153ef3454387ab"
    ;;
  *)
    echo "Unknown model key: $1" >&2
    exit 2
    ;;
esac

MODEL_RESULT="${RESULT_ROOT}/${ADAPTER}"
AUDIT_DIR="${MODEL_RESULT}/audit"
FINGERPRINT="${AUDIT_DIR}/model_fingerprint.json"

cd "$SRC_ROOT"
python -m pip install -r requirements-gpu.txt

python src/multijudge_workflows/model_preflight.py \
  --model-path "$MODEL_PATH" \
  --revision "$REVISION" \
  --require-cuda \
  --print-torchvision

mkdir -p "$AUDIT_DIR"
python -u src/multijudge_workflows/capture_runtime.py \
  --model-path "$MODEL_PATH" \
  --output-dir "$AUDIT_DIR"

COMMON=(
  --adapter "$ADAPTER"
  --dataset-root "$DATASET_ROOT"
  --manifest "$MANIFEST"
  --policy "$POLICY"
  --model-path "$MODEL_PATH"
  --model-revision "$REVISION"
  --model-fingerprint "$FINGERPRINT"
  --max-new-tokens 32
  --attn-implementation sdpa
  --max-parser-failure-rate 0
  --seed 20260725
)

python -u src/multijudge_workflows/run_native_judge.py \
  "${COMMON[@]}" \
  --limit 3 \
  --run-dir "${MODEL_RESULT}/qual3-a"

python -u src/multijudge_workflows/run_native_judge.py \
  "${COMMON[@]}" \
  --limit 3 \
  --run-dir "${MODEL_RESULT}/qual3-b"

python -u src/multijudge_workflows/compare_repeatability.py \
  --first "${MODEL_RESULT}/qual3-a/judgments.jsonl" \
  --second "${MODEL_RESULT}/qual3-b/judgments.jsonl" \
  --output "${MODEL_RESULT}/repeatability.json"

python -u src/multijudge_workflows/run_native_judge.py \
  "${COMMON[@]}" \
  --run-dir "${MODEL_RESULT}/smoke100"

echo "${ADAPTER} qualification completed successfully."
