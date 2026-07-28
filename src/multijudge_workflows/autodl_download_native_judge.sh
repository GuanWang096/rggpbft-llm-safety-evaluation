#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 safework|internvl|minicpm" >&2
  exit 2
fi

case "$1" in
  safework)
    REPO="AI45Research/SafeWork-RM-Safety-7B"
    REVISION="be345f29425fe94586c0598785a143703bbbc4fc"
    TARGET="/root/autodl-tmp/model/SafeWork-RM-Safety-7B"
    ;;
  internvl)
    REPO="OpenGVLab/InternVL3_5-8B-Instruct"
    REVISION="6c2034f6f3d22bbbff919b11b91c5721bba84f8d"
    TARGET="/root/autodl-tmp/model/InternVL3_5-8B-Instruct"
    ;;
  minicpm)
    REPO="OpenBMB/MiniCPM-V-4_5"
    REVISION="2626e837a54905aab70fae9325153ef3454387ab"
    TARGET="/root/autodl-tmp/model/MiniCPM-V-4_5"
    ;;
  *)
    echo "Unknown model key: $1" >&2
    exit 2
    ;;
esac

mkdir -p "$(dirname "$TARGET")"
echo "Downloading $REPO"
echo "Revision: $REVISION"
echo "Target: $TARGET"

modelscope download "$REPO" \
  --revision "$REVISION" \
  --local-dir "$TARGET" \
  --max-workers 8

test -f "$TARGET/config.json"
du -sh "$TARGET"
echo "Download completed."
