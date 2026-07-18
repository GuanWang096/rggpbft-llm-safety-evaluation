#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
REPO_ROOT=$(cd "$FABRIC_ROOT/../.." && pwd)
TDIR="$FABRIC_ROOT/fabric-samples/test-network"
E1_DIR=${1:-"$REPO_ROOT/results/e1-final-2048-topup"}
RUN_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR=${2:-"$REPO_ROOT/results/e1-anchor-$RUN_STAMP"}
TASK_ID="e1-qwen3vl4b-$RUN_STAMP"
SUBJECT_ID="Qwen3-VL-4B-Instruct"
DEADLINE=$(( $(date +%s) + 3600 ))

export PATH="$FABRIC_ROOT/fabric-samples/bin:$PATH"
export FABRIC_CFG_PATH="$FABRIC_ROOT/fabric-samples/config"
export CORE_PEER_TLS_ENABLED=true

ORDERER_CA="$TDIR/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"
PEER1_TLS="$TDIR/organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem"
PEER2_TLS="$TDIR/organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem"
PEER3_TLS="$TDIR/organizations/peerOrganizations/org3.example.com/tlsca/tlsca.org3.example.com-cert.pem"
PEER_ARGS=(
    --peerAddresses localhost:7051 --tlsRootCertFiles "$PEER1_TLS"
    --peerAddresses localhost:9051 --tlsRootCertFiles "$PEER2_TLS"
    --peerAddresses localhost:11051 --tlsRootCertFiles "$PEER3_TLS"
)
CORE_FILES=(
    checksums.sha256 config.json environment.json generation.jsonl
    generation_status.json moderation.jsonl moderation_config.json
    moderation_status.json summary.json generation_topup.jsonl
    topup_config.json topup_generation_status.json
)

mkdir -p "$RUN_DIR"
LOG_FILE="$RUN_DIR/workflow.log"
STATUS_FILE="$RUN_DIR/status.json"
printf '%s\n' '{"state":"running"}' >"$STATUS_FILE"
on_error() {
    local status=$?
    trap - ERR
    printf '{"state":"failed","exit_status":%d}\n' "$status" >"$STATUS_FILE"
    exit "$status"
}
trap on_error ERR
exec > >(tee "$LOG_FILE") 2>&1

set_org_admin() {
    local org=$1
    local port=$2
    export CORE_PEER_LOCALMSPID="${org^}MSP"
    export CORE_PEER_TLS_ROOTCERT_FILE="$TDIR/organizations/peerOrganizations/${org}.example.com/peers/peer0.${org}.example.com/tls/ca.crt"
    export CORE_PEER_MSPCONFIGPATH="$TDIR/organizations/peerOrganizations/${org}.example.com/users/Admin@${org}.example.com/msp"
    export CORE_PEER_ADDRESS="localhost:${port}"
}

set_org1_identity() {
    local identity=$1
    export CORE_PEER_LOCALMSPID=Org1MSP
    export CORE_PEER_TLS_ROOTCERT_FILE="$TDIR/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
    export CORE_PEER_MSPCONFIGPATH="$TDIR/organizations/peerOrganizations/org1.example.com/users/${identity}/msp"
    export CORE_PEER_ADDRESS=localhost:7051
}

cc_payload() {
    python3 -c 'import json,sys; print(json.dumps({"Args": sys.argv[1:]}, separators=(",", ":")))' "$@"
}

invoke() {
    local payload
    payload=$(cc_payload "$@")
    peer chaincode invoke -o localhost:7050 \
        --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" \
        -C trustchannel -n tce "${PEER_ARGS[@]}" --waitForEvent --waitForEventTimeout 60s \
        -c "$payload"
}

query() {
    local payload
    payload=$(cc_payload "$@")
    peer chaincode query -C trustchannel -n tce -c "$payload"
}

for file in "${CORE_FILES[@]}"; do
    [[ -f "$E1_DIR/$file" ]] || { echo "Missing E1 artifact: $E1_DIR/$file" >&2; exit 1; }
done
(cd "$E1_DIR" && sha256sum -c checksums.sha256)

python3 - "$E1_DIR" "$RUN_DIR/evidence_manifest.json" "${CORE_FILES[@]}" <<'PY'
import hashlib
import json
import pathlib
import statistics
import sys

root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
included_files = set(sys.argv[3:])
summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
config = json.loads((root / "config.json").read_text(encoding="utf-8"))
overall = summary["groups"]["overall"]
max_new_tokens = int(config["max_new_tokens"])
latencies = {"generation_ms": [], "moderation_ms": []}
cap_hits = 0
for path, key in ((root / "generation.jsonl", "generation_ms"), (root / "moderation.jsonl", "moderation_ms")):
    for line in path.open(encoding="utf-8"):
        row = json.loads(line)
        latencies[key].append(row["latency_ms"])
        if key == "generation_ms" and int(row.get("output_tokens", 0)) >= max_new_tokens:
            cap_hits += 1

files = {}
for path in sorted(root.iterdir()):
    if path.is_file() and path.name in included_files:
        files[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

def ppm(value):
    return int(round(value * 1_000_000))

manifest = {
    "schema": "zte-sci-e1-evidence-v1",
    "models": {
        "generator": "Qwen3-VL-4B-Instruct",
        "moderator": "Qwen3Guard-Gen-4B",
    },
    "datasets": {"MM-SafetyBench-Tiny": 504, "VLGuard-test": 1558},
    "sample_count": overall["sample_count"],
    "generation_budget": {
        "strategy": config.get("strategy", "fixed"),
        "initial_max_new_tokens": config.get("initial_max_new_tokens"),
        "max_new_tokens": max_new_tokens,
        "cap_hit_count": cap_hits,
    },
    "metrics": {
        "safety_pass_rate_ppm": ppm(1 - overall["attack_success_rate"]),
        "unsafe_input_refusal_rate_ppm": ppm(overall["unsafe_input_refusal_rate"]),
        "guard_safe_non_refusal_rate_ppm": ppm(overall["safe_input_utility_rate"]),
    },
    "latency_ms": {
        key: {
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "p95": sorted(values)[int(0.95 * (len(values) - 1))],
        }
        for key, values in latencies.items()
    },
    "attestation_scope": (
        "Three Fabric service identities attest deterministic metric dimensions "
        "computed from one frozen evidence bundle; they are not independent model judges."
    ),
    "files": files,
}
output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

BUNDLE="$RUN_DIR/e1_evidence_bundle.tar.gz"
tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
    -czf "$BUNDLE" -C "$E1_DIR" "${CORE_FILES[@]}" -C "$RUN_DIR" evidence_manifest.json
BUNDLE_SHA=$(sha256sum "$BUNDLE" | awk '{print $1}')
BUNDLE_BYTES=$(stat -c %s "$BUNDLE")
CID=$(curl --fail --silent --show-error -X POST -F "file=@$BUNDLE" http://localhost:5001/api/v0/add | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["Hash"])')
curl --fail --silent --show-error -X POST "http://localhost:5001/api/v0/cat?arg=$CID" -o "$RUN_DIR/ipfs_retrieved.tar.gz"
[[ "$(sha256sum "$RUN_DIR/ipfs_retrieved.tar.gz" | awk '{print $1}')" == "$BUNDLE_SHA" ]]

mapfile -t SCORES < <(python3 - "$RUN_DIR/evidence_manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("safety_pass_rate_ppm", "unsafe_input_refusal_rate_ppm", "guard_safe_non_refusal_rate_ppm"):
    print(m["metrics"][key])
PY
)

EVAL_IDS=("${TASK_ID}-safety" "${TASK_ID}-refusal" "${TASK_ID}-safe-nonrefusal")
EVAL_USERS=(evaluator-e1 evaluator-e2 evaluator-e3)
EVAL_CAPS=(safety-pass unsafe-refusal guard-safe-nonrefusal-proxy)
CLIENT_IDS=()
MSP_IDS=()

for i in 0 1 2; do
    set_org1_identity "${EVAL_USERS[$i]}"
    register_json=$(python3 -c 'import json,sys; print(json.dumps({"evalId":sys.argv[1],"capabilities":[sys.argv[2]]}, separators=(",",":")))' "${EVAL_IDS[$i]}" "${EVAL_CAPS[$i]}")
    invoke RegisterEvaluator "$register_json"
    evaluator_json=$(query QueryEvaluator "${EVAL_IDS[$i]}")
    printf '%s\n' "$evaluator_json" > "$RUN_DIR/evaluator_$((i+1))_registered.json"
    CLIENT_IDS+=("$(python3 -c 'import json,sys; print(json.load(sys.stdin)["clientId"])' <<<"$evaluator_json")")
    MSP_IDS+=("$(python3 -c 'import json,sys; print(json.load(sys.stdin)["mspId"])' <<<"$evaluator_json")")
done

set_org_admin org1 7051
task_json=$(python3 - "$TASK_ID" "$SUBJECT_ID" "$DEADLINE" "$BUNDLE_BYTES" "$CID" "$BUNDLE_SHA" <<'PY'
import json, sys
task_id, subject_id, deadline, size, cid, sha = sys.argv[1:]
print(json.dumps({
    "taskId": task_id, "subjectId": subject_id,
    "riskCategories": ["multimodal-safety"], "modalities": ["text", "image"],
    "workload": 2062, "deadlineUnix": int(deadline), "inputBytes": int(size),
    "priority": 5, "minEvaluators": 3, "minReputationPpm": 400000,
    "cid": cid, "sha256": sha,
}, separators=(",", ":")))
PY
)
invoke PostTaskConstraint "$task_json"

for spec in "org1 7051" "org2 9051" "org3 11051"; do
    read -r org port <<<"$spec"
    set_org_admin "$org" "$port"
    query QueryTask "$TASK_ID" > "$RUN_DIR/task_${org}.json"
done
TASK_HASHES=$(sha256sum "$RUN_DIR"/task_org*.json | awk '{print $1}' | sort -u | wc -l)
[[ "$TASK_HASHES" -eq 1 ]]

set_org1_identity audit-service
allocation_json=$(python3 - "$TASK_ID" "${EVAL_IDS[@]}" <<'PY'
import json, sys
shares = [333334, 333333, 333333]
print(json.dumps({"taskId":sys.argv[1], "members":[
    {"evalId":eval_id, "sharePpm":share} for eval_id, share in zip(sys.argv[2:], shares)
]}, separators=(",", ":")))
PY
)
invoke PostAllocation "$allocation_json"

snapshot_json=$(python3 - "$TASK_ID" "$DEADLINE" "$CID" "$BUNDLE_SHA" \
    "${EVAL_IDS[0]}" "${SCORES[0]}" "${CLIENT_IDS[0]}" "${MSP_IDS[0]}" \
    "${EVAL_IDS[1]}" "${SCORES[1]}" "${CLIENT_IDS[1]}" "${MSP_IDS[1]}" \
    "${EVAL_IDS[2]}" "${SCORES[2]}" "${CLIENT_IDS[2]}" "${MSP_IDS[2]}" <<'PY'
import json, sys
task_id, deadline, cid, sha = sys.argv[1:5]
values = sys.argv[5:]
items, refs = [], []
for offset in range(0, len(values), 4):
    eval_id, score, client_id, msp_id = values[offset:offset+4]
    items.append({"evalId":eval_id, "scorePpm":int(score), "verdict":"attested"})
    refs.append({"evalId":eval_id, "taskId":task_id, "cid":cid, "sha256":sha,
                 "submitterClientId":client_id, "submitterMspId":msp_id})
print(json.dumps({"taskId":task_id, "evalItems":items, "evidenceRefs":refs,
                  "deadlineUnix":int(deadline)}, separators=(",", ":")))
PY
)
invoke PostEvalSnapshot "$snapshot_json"
confirmation_json=$(query QueryConfirmation "$TASK_ID")
DIGEST=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["digest"])' <<<"$confirmation_json")

for i in 0 1; do
    set_org1_identity "${EVAL_USERS[$i]}"
    invoke SubmitVote "$TASK_ID" "$DIGEST" ACK
done

set_org1_identity audit-service
query QueryConfirmation "$TASK_ID" > "$RUN_DIR/confirmation_accepted.json"
invoke ProcessSettlement "$TASK_ID"
query QueryAllocation "$TASK_ID" > "$RUN_DIR/allocation_settled.json"
query QueryConfirmation "$TASK_ID" > "$RUN_DIR/confirmation_settled.json"
query QueryTaskReputation "$SUBJECT_ID" "$TASK_ID" > "$RUN_DIR/task_reputation.json"
for i in 0 1 2; do
    query QueryEvaluator "${EVAL_IDS[$i]}" > "$RUN_DIR/evaluator_$((i+1))_settled.json"
done

python3 - "$RUN_DIR" "$TASK_ID" "$CID" "$BUNDLE_SHA" "$BUNDLE_BYTES" <<'PY'
import json, pathlib, sys
run = pathlib.Path(sys.argv[1])
confirmation = json.loads((run / "confirmation_settled.json").read_text())
allocation = json.loads((run / "allocation_settled.json").read_text())
reputation = json.loads((run / "task_reputation.json").read_text())
assert confirmation["status"] == "Accept" and confirmation["consumed"] is True
assert allocation["status"] == "Settled"
record = {
    "task_id": sys.argv[2], "ipfs_cid": sys.argv[3], "bundle_sha256": sys.argv[4],
    "bundle_bytes": int(sys.argv[5]), "confirmation": confirmation,
    "allocation": allocation, "task_reputation": reputation,
}
(run / "lifecycle_result.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
PY

printf '%s\n' '{"state":"completed"}' >"$STATUS_FILE"
trap - ERR
(cd "$RUN_DIR" && sha256sum evidence_manifest.json e1_evidence_bundle.tar.gz ipfs_retrieved.tar.gz \
    task_org1.json task_org2.json task_org3.json confirmation_accepted.json \
    confirmation_settled.json allocation_settled.json task_reputation.json \
    lifecycle_result.json status.json > artifact_checksums.sha256)

echo "E1 evidence lifecycle completed"
echo "run_dir=$RUN_DIR"
echo "task_id=$TASK_ID"
echo "cid=$CID"
echo "sha256=$BUNDLE_SHA"
