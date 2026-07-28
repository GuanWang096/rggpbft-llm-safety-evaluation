#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 val|test qwen|safework|internvl|minicpm|all" >&2
  exit 2
fi

SPLIT="$1"
MODEL_KEY="$2"
if [[ "$SPLIT" != "val" && "$SPLIT" != "test" ]]; then
  echo "Split must be val or test" >&2
  exit 2
fi
if [[ "$SPLIT" == "test" && ! -f /root/result/multijudge-v15/formal/validation_frozen.json ]]; then
  echo "Test is locked until validation_frozen.json is supplied." >&2
  exit 3
fi

export OMP_NUM_THREADS=8
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PYTHONPATH="/root/src/src${PYTHONPATH:+:${PYTHONPATH}}"

SRC=/root/src
DATA=/root/datasets
RESULT=/root/result/multijudge-v15/formal
MANIFEST="$SRC/src/multijudge_workflows/configs/mmds_${SPLIT}_formal.json"
POLICY="$SRC/src/multijudge_workflows/configs/canonical_policy_v1.json"
SEED=20260726

run_one() {
  local key="$1"
  local run_dir="$RESULT/$SPLIT/$key"
  mkdir -p "$run_dir"
  case "$key" in
    qwen)
      python -u "$SRC/src/multijudge_workflows/run_judge.py" \
        --dataset-root "$DATA" \
        --manifest "$MANIFEST" \
        --policy "$POLICY" \
        --model-path /root/autodl-tmp/model/Qwen3-VL-8B-Instruct \
        --model-revision 5d854aab08710c16b980ec6d603d863b3821b915 \
        --model-fingerprint /root/result/multijudge-v15/qwen-audit/model_fingerprint.json \
        --run-dir "$run_dir" \
        --input-mode native \
        --max-new-tokens 96 \
        --attn-implementation sdpa \
        --max-parser-failure-rate 0 \
        --seed "$SEED"
      ;;
    safework|internvl|minicpm)
      local model_path revision
      case "$key" in
        safework)
          model_path=/root/autodl-tmp/model/SafeWork-RM-Safety-7B
          revision=be345f29425fe94586c0598785a143703bbbc4fc
          ;;
        internvl)
          model_path=/root/autodl-tmp/model/InternVL3_5-8B-Instruct
          revision=6c2034f6f3d22bbbff919b11b91c5721bba84f8d
          ;;
        minicpm)
          model_path=/root/autodl-tmp/model/MiniCPM-V-4_5
          revision=2626e837a54905aab70fae9325153ef3454387ab
          ;;
      esac
      python -u "$SRC/src/multijudge_workflows/run_native_judge.py" \
        --adapter "$key" \
        --dataset-root "$DATA" \
        --manifest "$MANIFEST" \
        --policy "$POLICY" \
        --model-path "$model_path" \
        --model-revision "$revision" \
        --model-fingerprint "/root/result/multijudge-v15/native-judges/$key/audit/model_fingerprint.json" \
        --run-dir "$run_dir" \
        --max-new-tokens 32 \
        --attn-implementation sdpa \
        --max-parser-failure-rate 0 \
        --seed "$SEED"
      ;;
    *)
      echo "Unknown model key: $key" >&2
      exit 2
      ;;
  esac
}

if [[ "$MODEL_KEY" == "all" ]]; then
  for key in qwen safework internvl minicpm; do
    run_one "$key"
  done
  python -u "$SRC/src/multijudge_workflows/validate_mj1_outputs.py" \
    --manifest "$MANIFEST" \
    --model "qwen=$RESULT/$SPLIT/qwen" \
    --model "safework=$RESULT/$SPLIT/safework" \
    --model "internvl=$RESULT/$SPLIT/internvl" \
    --model "minicpm=$RESULT/$SPLIT/minicpm" \
    --output "$RESULT/$SPLIT/acceptance.json"
else
  run_one "$MODEL_KEY"
fi

echo "MJ1 $SPLIT $MODEL_KEY completed successfully."
