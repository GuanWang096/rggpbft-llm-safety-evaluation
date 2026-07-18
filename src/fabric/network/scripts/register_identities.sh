#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
FABRIC_SAMPLES="$FABRIC_ROOT/fabric-samples"
BINDIR="$FABRIC_SAMPLES/bin"
export PATH="$BINDIR:$PATH"
TDIR="$FABRIC_SAMPLES/test-network"
CA_CERT=$TDIR/organizations/fabric-ca/org1/ca-cert.pem

# Step 1: Start Org1 CA
echo "=== Start Org1 CA ==="
cd "$TDIR"
DOCKER_SOCK=/var/run/docker.sock docker compose -f compose/compose-ca.yaml up -d ca_org1 2>&1
sleep 3

# Step 2: Enroll the CA admin (use temp home to avoid overwriting peer MSP)
echo "=== Enroll CA admin ==="
ADMIN_HOME=$TDIR/organizations/fabric-ca/org1/admin
mkdir -p "$ADMIN_HOME"
export FABRIC_CA_CLIENT_HOME=$ADMIN_HOME
fabric-ca-client enroll -u https://admin:adminpw@localhost:7054 \
  --caname ca-org1 --tls.certfiles "$CA_CERT" 2>&1

# Step 3: Register new identities
echo ""
echo "=== Register audit_service (role=audit_service:ecert) ==="
fabric-ca-client register --caname ca-org1 \
  --id.name audit-service --id.secret auditpw \
  --id.type client \
  --id.attrs 'role=audit_service:ecert' \
  --tls.certfiles "$CA_CERT" 2>&1

echo ""
echo "=== Enroll audit_service ==="
AUDIT_MSP=$TDIR/organizations/peerOrganizations/org1.example.com/users/audit-service/msp
fabric-ca-client enroll -u https://audit-service:auditpw@localhost:7054 \
  --caname ca-org1 \
  -M "$AUDIT_MSP" \
  --tls.certfiles "$CA_CERT" 2>&1
mkdir -p "$AUDIT_MSP/admincerts"
cp $TDIR/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp/admincerts/*.pem "$AUDIT_MSP/admincerts/"
echo "audit_service enrolled"

# Step 4: Register evaluator client identities
echo ""
for i in 1 2 3; do
  idname="evaluator-e$i"
  echo "=== Register $idname ==="
  fabric-ca-client register --caname ca-org1 \
    --id.name "$idname" --id.secret "evalpw$i" \
    --id.type client \
    --tls.certfiles "$CA_CERT" 2>&1

  EVAL_MSP="$TDIR/organizations/peerOrganizations/org1.example.com/users/${idname}/msp"
  fabric-ca-client enroll -u "https://${idname}:evalpw${i}@localhost:7054" \
    --caname ca-org1 \
    -M "$EVAL_MSP" \
    --tls.certfiles "$CA_CERT" 2>&1

  mkdir -p "$EVAL_MSP/admincerts"
  cp $TDIR/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp/admincerts/*.pem "$EVAL_MSP/admincerts/"
  echo "$idname enrolled"
done

# Step 5: Verify
echo ""
echo "=== Verify audit_service can query ==="
export FABRIC_CFG_PATH=$TDIR/../config
export CORE_PEER_TLS_ENABLED=true
export CORE_PEER_LOCALMSPID=Org1MSP
export CORE_PEER_TLS_ROOTCERT_FILE=$TDIR/organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt
export CORE_PEER_MSPCONFIGPATH=$AUDIT_MSP
export CORE_PEER_ADDRESS=localhost:7051

ORDERER_CA=$TDIR/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem
PEER1_TLS=$TDIR/organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem
PEER2_TLS=$TDIR/organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem

echo "Querying t1 as audit_service..."
peer chaincode query -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com --tls \
  --cafile "$ORDERER_CA" -C trustchannel -n tce \
  -c '{"Args":["QueryTask","t1"]}' 2>&1

echo ""
echo "=== Test PostAllocation (requires audit_service role) ==="
peer chaincode invoke -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" \
  -C trustchannel -n tce \
  --peerAddresses localhost:7051 --tlsRootCertFiles "$PEER1_TLS" \
  --peerAddresses localhost:9051 --tlsRootCertFiles "$PEER2_TLS" \
  -c '{"Args":["PostAllocation","{\"taskId\":\"t1\",\"members\":[{\"evalId\":\"e1\",\"sharePpm\":500000},{\"evalId\":\"e2\",\"sharePpm\":500000}]}"]}' 2>&1

echo ""
echo "=== Also test PostAllocation fails with regular admin ==="
export CORE_PEER_MSPCONFIGPATH=$TDIR/organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp
peer chaincode invoke -o localhost:7050 \
  --ordererTLSHostnameOverride orderer.example.com --tls --cafile "$ORDERER_CA" \
  -C trustchannel -n tce \
  --peerAddresses localhost:7051 --tlsRootCertFiles "$PEER1_TLS" \
  --peerAddresses localhost:9051 --tlsRootCertFiles "$PEER2_TLS" \
  -c '{"Args":["PostAllocation","{\"taskId\":\"t1\",\"members\":[{\"evalId\":\"e1\",\"sharePpm\":500000},{\"evalId\":\"e2\",\"sharePpm\":500000}]}"]}' 2>&1

echo ""
echo "=== DONE ==="
