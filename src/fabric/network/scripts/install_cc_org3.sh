#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
FABRIC_SAMPLES="$FABRIC_ROOT/fabric-samples"
BINDIR="$FABRIC_SAMPLES/bin"
export PATH="$BINDIR:$PATH"
export FABRIC_CFG_PATH="$FABRIC_SAMPLES/config"
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_LOCALMSPID=Org3MSP
export CORE_PEER_TLS_ROOTCERT_FILE="$FABRIC_SAMPLES/test-network/organizations/peerOrganizations/org3.example.com/peers/peer0.org3.example.com/tls/ca.crt"
export CORE_PEER_MSPCONFIGPATH="$FABRIC_SAMPLES/test-network/organizations/peerOrganizations/org3.example.com/users/Admin@org3.example.com/msp"
export CORE_PEER_ADDRESS=localhost:11051

TDIR="$FABRIC_SAMPLES/test-network"
ORDERER_CA=$TDIR/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem
PEER1_TLS=$TDIR/organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem
PEER2_TLS=$TDIR/organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem
PEER3_TLS=$TDIR/organizations/peerOrganizations/org3.example.com/tlsca/tlsca.org3.example.com-cert.pem
PACKAGE_ID="tce_2.0:3cb4d66a065c0f37b23bd21026804fa4b5b6764d2711952548678d05c0b754a7"

cd "$TDIR"

echo "=== Install chaincode on peer0.org3 ==="
peer lifecycle chaincode install tce.tar.gz 2>&1

echo ""
echo "=== Approve for Org3MSP (same version/sequence) ==="
peer lifecycle chaincode approveformyorg -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" \
  --channelID trustchannel --name tce --version 2.0 \
  --package-id "$PACKAGE_ID" --sequence 1 2>&1

echo ""
echo "=== Check commit readiness (expect all 3 true) ==="
peer lifecycle chaincode checkcommitreadiness --channelID trustchannel \
  --name tce --version 2.0 --sequence 1 --output json 2>&1

echo ""
echo "=== Query committed on Org3 ==="
peer lifecycle chaincode querycommitted --channelID trustchannel --name tce 2>&1

echo ""
echo "=== Test: Register e3 via tri-org endorsement ==="
peer chaincode invoke -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" \
  -C trustchannel -n tce \
  --peerAddresses localhost:7051 --tlsRootCertFiles "$PEER1_TLS" \
  --peerAddresses localhost:9051 --tlsRootCertFiles "$PEER2_TLS" \
  --peerAddresses localhost:11051 --tlsRootCertFiles "$PEER3_TLS" \
  -c '{"Args":["RegisterEvaluator","{\"evalId\":\"e3\",\"capabilities\":[\"text\",\"vision\"]}"]}' 2>&1

echo ""
echo "=== Query task t1 from Org3 ==="
peer chaincode query -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" \
  -C trustchannel -n tce \
  -c '{"Args":["QueryTask","t1"]}' 2>&1

echo ""
echo "=== DONE ==="
