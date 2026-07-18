#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
TDIR="$FABRIC_ROOT/fabric-samples/test-network"

required_containers=(
    orderer.example.com
    peer0.org1.example.com
    peer0.org2.example.com
    peer0.org3.example.com
    couchdb0
    couchdb1
    couchdb4
    ca_org1
    ipfs-kubo
)

for container in "${required_containers[@]}"; do
    running=$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)
    if [[ "$running" != "true" ]]; then
        echo "required container is not running: $container" >&2
        exit 1
    fi
done

ipfs_json=$(curl --fail --silent --show-error -X POST http://localhost:5001/api/v0/id)
python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["ID"] and value["AgentVersion"]' <<<"$ipfs_json"

export PATH="$FABRIC_ROOT/fabric-samples/bin:$PATH"
export FABRIC_CFG_PATH="$FABRIC_ROOT/fabric-samples/config"
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_LOCALMSPID=Org1MSP
export CORE_PEER_TLS_ROOTCERT_FILE="$TDIR/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
export CORE_PEER_MSPCONFIGPATH="$TDIR/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp"
export CORE_PEER_ADDRESS=localhost:7051

channel_info=$(peer channel getinfo -c trustchannel 2>&1)
grep -q 'Blockchain info:' <<<"$channel_info"

committed=$(peer lifecycle chaincode querycommitted -C trustchannel 2>&1)
grep -q 'Name: tce' <<<"$committed"

set +e
query_output=$(peer chaincode query -C trustchannel -n tce \
    -c '{"Args":["QueryTask","__preflight_missing_task__"]}' 2>&1)
query_status=$?
set -e
if (( query_status == 0 )); then
    echo "preflight sentinel unexpectedly exists" >&2
    exit 1
fi
grep -q 'ERR_TASK_NOT_FOUND: __preflight_missing_task__' <<<"$query_output"

python3 - "$channel_info" "$committed" "$ipfs_json" <<'PY'
import json
import sys

channel_info, committed, ipfs_raw = sys.argv[1:]
ipfs = json.loads(ipfs_raw)
print(json.dumps({
    "state": "healthy",
    "channel": "trustchannel",
    "channel_info": channel_info,
    "chaincode": committed,
    "ipfs_agent": ipfs["AgentVersion"],
}, sort_keys=True))
PY
