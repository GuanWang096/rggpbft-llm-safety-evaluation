#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
TDIR="$FABRIC_ROOT/fabric-samples/test-network"
TASK_ID=${1:?task id is required}
OUTPUT=${2:?output path is required}

export PATH="$FABRIC_ROOT/fabric-samples/bin:$PATH"
export FABRIC_CFG_PATH="$FABRIC_ROOT/fabric-samples/config"
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_LOCALMSPID=Org1MSP
export CORE_PEER_TLS_ROOTCERT_FILE="$TDIR/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
export CORE_PEER_MSPCONFIGPATH="$TDIR/organizations/peerOrganizations/org1.example.com/users/audit-service/msp"
export CORE_PEER_ADDRESS=localhost:7051

ORDERER_CA="$TDIR/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"
PEER1_TLS="$TDIR/organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem"
PEER2_TLS="$TDIR/organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem"
PEER3_TLS="$TDIR/organizations/peerOrganizations/org3.example.com/tlsca/tlsca.org3.example.com-cert.pem"
PAYLOAD=$(python3 -c 'import json,sys; print(json.dumps({"Args":["ProcessSettlement",sys.argv[1]]},separators=(",",":")))' "$TASK_ID")

set +e
peer chaincode invoke -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" \
  -C trustchannel -n tce \
  --peerAddresses localhost:7051 --tlsRootCertFiles "$PEER1_TLS" \
  --peerAddresses localhost:9051 --tlsRootCertFiles "$PEER2_TLS" \
  --peerAddresses localhost:11051 --tlsRootCertFiles "$PEER3_TLS" \
  --waitForEvent --waitForEventTimeout 60s -c "$PAYLOAD" >"$OUTPUT" 2>&1
STATUS=$?
set -e

if [[ $STATUS -eq 0 ]]; then
  echo "duplicate settlement was unexpectedly accepted" >>"$OUTPUT"
  exit 1
fi
echo "duplicate settlement rejected with exit status $STATUS" >>"$OUTPUT"
