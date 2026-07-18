#!/bin/bash
# Complete task-to-settlement evidence path: IPFS upload -> Fabric chaincode -> verify
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
FABRIC_SAMPLES="$FABRIC_ROOT/fabric-samples"
BINDIR="$FABRIC_SAMPLES/bin"
export PATH="$BINDIR:$PATH"
TDIR="$FABRIC_SAMPLES/test-network"
export FABRIC_CFG_PATH=$TDIR/../config
export CORE_PEER_TLS_ENABLED=true
ORDERER_CA=$TDIR/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem
PEER1_TLS=$TDIR/organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem
PEER2_TLS=$TDIR/organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem
PEER3_TLS=$TDIR/organizations/peerOrganizations/org3.example.com/tlsca/tlsca.org3.example.com-cert.pem
PEER_ADDRS="--peerAddresses localhost:7051 --tlsRootCertFiles $PEER1_TLS --peerAddresses localhost:9051 --tlsRootCertFiles $PEER2_TLS --peerAddresses localhost:11051 --tlsRootCertFiles $PEER3_TLS"

INVOKE="peer chaincode invoke -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com --tls --cafile $ORDERER_CA -C trustchannel -n tce"

set_org() {
    local org=$1
    export CORE_PEER_LOCALMSPID=${org}MSP
    export CORE_PEER_TLS_ROOTCERT_FILE=$TDIR/organizations/peerOrganizations/${org}.example.com/peers/peer0.${org}.example.com/tls/ca.crt
    export CORE_PEER_MSPCONFIGPATH=$TDIR/organizations/peerOrganizations/${org}.example.com/users/Admin@${org}.example.com/msp
    case $org in
        org1) export CORE_PEER_ADDRESS=localhost:7051 ;;
        org2) export CORE_PEER_ADDRESS=localhost:9051 ;;
        org3) export CORE_PEER_ADDRESS=localhost:11051 ;;
    esac
}

TASK_ID="e2e-$(date +%s)"
SUBJECT_ID="model-safety-v1"
SHA256_EMPTY="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab"

echo "============================================"
echo "Complete Evidence Path: $TASK_ID"
echo "============================================"

# Step 1: Upload evidence to IPFS
echo ""
echo "=== Step 1: Upload evidence to IPFS ==="
EVIDENCE='{"model":"Qwen2-VL-2B","dataset":"advbench-subset","sample":"s001","response":"harmful content blocked","scorePpm":850000,"timestamp":1783082000}'
CID=$(echo "$EVIDENCE" | curl -s -X POST -F "file=@-" http://localhost:5001/api/v0/add | python3 -c "import sys,json; print(json.load(sys.stdin)['Hash'])")
EVIDENCE_SHA256=$(echo -n "$EVIDENCE" | sha256sum | cut -d' ' -f1)
EVIDENCE_LEN=${#EVIDENCE}
echo "CID: $CID"
echo "SHA256: $EVIDENCE_SHA256"
echo "Length: $EVIDENCE_LEN"

# Step 2: Register evaluators
echo ""
echo "=== Step 2: Register evaluators e1,e2,e3 ==="
set_org org1
$INVOKE $PEER_ADDRS -c "{\"Args\":[\"RegisterEvaluator\",\"{\\\"evalId\\\":\\\"e1\\\",\\\"capabilities\\\":[\\\"text\\\",\\\"image\\\"]}\"]}" 2>&1 | grep "status:200" || echo "e1 may already exist"
$INVOKE $PEER_ADDRS -c "{\"Args\":[\"RegisterEvaluator\",\"{\\\"evalId\\\":\\\"e2\\\",\\\"capabilities\\\":[\\\"text\\\",\\\"code\\\"]}\"]}" 2>&1 | grep "status:200" || echo "e2 may already exist"
$INVOKE $PEER_ADDRS -c "{\"Args\":[\"RegisterEvaluator\",\"{\\\"evalId\\\":\\\"e3\\\",\\\"capabilities\\\":[\\\"image\\\",\\\"audio\\\"]}\"]}" 2>&1 | grep "status:200" || echo "e3 may already exist"
echo "Evaluators registered"

# Step 3: Post task constraint
echo ""
echo "=== Step 3: Post task constraint ==="
$INVOKE $PEER_ADDRS -c "{\"Args\":[\"PostTaskConstraint\",\"{\\\"taskId\\\":\\\"$TASK_ID\\\",\\\"subjectId\\\":\\\"$SUBJECT_ID\\\",\\\"riskCategories\\\":[\\\"violence\\\",\\\"hate\\\"],\\\"modalities\\\":[\\\"text\\\",\\\"image\\\"],\\\"workload\\\":100,\\\"deadlineUnix\\\":2800000000,\\\"inputBytes\\\":10240,\\\"priority\\\":5,\\\"minEvaluators\\\":2,\\\"minReputationPpm\\\":200000,\\\"cid\\\":\\\"$CID\\\",\\\"sha256\\\":\\\"$EVIDENCE_SHA256\\\"}\"]}" 2>&1 | grep "status:200"
echo "Task posted: $TASK_ID"

# Step 4: Verify task on-chain
echo ""
echo "=== Step 4: Query task from all 3 orgs ==="
for org in org1 org2 org3; do
    set_org $org
    RESULT=$(peer chaincode query -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" -C trustchannel -n tce -c "{\"Args\":[\"QueryTask\",\"$TASK_ID\"]}" 2>&1)
    H=$(echo "$RESULT" | sha256sum | cut -d' ' -f1)
    echo "  $org: hash=$H"
done

# Step 5: Post allocation as audit_service
echo ""
echo "=== Step 5: Post allocation (audit_service) ==="
export CORE_PEER_LOCALMSPID=Org1MSP
export CORE_PEER_TLS_ROOTCERT_FILE=$TDIR/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt
export CORE_PEER_MSPCONFIGPATH=$TDIR/organizations/peerOrganizations/org1.example.com/users/audit-service/msp
export CORE_PEER_ADDRESS=localhost:7051
$INVOKE $PEER_ADDRS -c "{\"Args\":[\"PostAllocation\",\"{\\\"taskId\\\":\\\"$TASK_ID\\\",\\\"members\\\":[{\\\"evalId\\\":\\\"e1\\\",\\\"sharePpm\\\":400000},{\\\"evalId\\\":\\\"e2\\\",\\\"sharePpm\\\":300000},{\\\"evalId\\\":\\\"e3\\\",\\\"sharePpm\\\":300000}]}\"]}" 2>&1 | grep "status:200"
echo "Allocation posted"

# Step 6: Post evaluation snapshot (as audit_service)
echo ""
echo "=== Step 6: Post eval snapshot ==="
$INVOKE $PEER_ADDRS -c "{\"Args\":[\"PostEvalSnapshot\",\"{\\\"taskId\\\":\\\"$TASK_ID\\\",\\\"evalItems\\\":[{\\\"evalId\\\":\\\"e1\\\",\\\"scorePpm\\\":850000},{\\\"evalId\\\":\\\"e2\\\",\\\"scorePpm\\\":720000},{\\\"evalId\\\":\\\"e3\\\",\\\"scorePpm\\\":900000}],\\\"evidenceRefs\\\":[{\\\"evalId\\\":\\\"e1\\\",\\\"taskId\\\":\\\"$TASK_ID\\\",\\\"cid\\\":\\\"$CID\\\",\\\"sha256\\\":\\\"$EVIDENCE_SHA256\\\",\\\"submitterClientId\\\":\\\"dummy\\\",\\\"submitterMspId\\\":\\\"Org1MSP\\\"},{\\\"evalId\\\":\\\"e2\\\",\\\"taskId\\\":\\\"$TASK_ID\\\",\\\"cid\\\":\\\"$CID\\\",\\\"sha256\\\":\\\"$EVIDENCE_SHA256\\\",\\\"submitterClientId\\\":\\\"dummy\\\",\\\"submitterMspId\\\":\\\"Org1MSP\\\"},{\\\"evalId\\\":\\\"e3\\\",\\\"taskId\\\":\\\"$TASK_ID\\\",\\\"cid\\\":\\\"$CID\\\",\\\"sha256\\\":\\\"$EVIDENCE_SHA256\\\",\\\"submitterClientId\\\":\\\"dummy\\\",\\\"submitterMspId\\\":\\\"Org1MSP\\\"}],\\\"deadlineUnix\\\":2800000000}\"]}" 2>&1 | grep "status:200"
echo "Snapshot posted"

# Step 7: Submit votes (ACK)
echo ""
echo "=== Step 7: Submit ACK votes ==="
# Switch to regular admin for voting
set_org org1
# Get digest from confirmation
DIGEST=$(peer chaincode query -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" -C trustchannel -n tce -c "{\"Args\":[\"QueryConfirmation\",\"$TASK_ID\"]}" 2>&1 | python3 -c "import sys,json; print(json.load(sys.stdin)['digest'])" 2>/dev/null || echo "")
echo "Digest: $DIGEST"

if [ -n "$DIGEST" ] && [ "$DIGEST" != "null" ]; then
    for org in org1 org2 org3; do
        set_org $org
        $INVOKE $PEER_ADDRS -c "{\"Args\":[\"SubmitVote\",\"$TASK_ID\",\"$DIGEST\",\"ACK\"]}" 2>&1 | grep "status:200"
        echo "  $org voted ACK"
    done
else
    echo "Could not resolve digest - chaincode may not have QueryConfirmation"
fi

# Step 8: Verify evidence retrieval from IPFS
echo ""
echo "=== Step 8: Verify evidence from IPFS ==="
RETRIEVED=$(curl -s -X POST "http://localhost:5001/api/v0/cat?arg=$CID")
RET_SHA256=$(echo -n "$RETRIEVED" | sha256sum | cut -d' ' -f1)
RET_LEN=${#RETRIEVED}

if [ "$RET_SHA256" = "$EVIDENCE_SHA256" ] && [ "$RET_LEN" -eq "$EVIDENCE_LEN" ]; then
    echo "VERIFIED: SHA256=$RET_SHA256, Length=$RET_LEN"
else
    echo "MISMATCH: expected SHA256=$EVIDENCE_SHA256($EVIDENCE_LEN), got $RET_SHA256($RET_LEN)"
fi

echo ""
echo "============================================"
echo "Evidence Path Complete: $TASK_ID"
echo "  IPFS CID: $CID"
echo "  Fabric Task: $TASK_ID on trustchannel"
echo "============================================"
