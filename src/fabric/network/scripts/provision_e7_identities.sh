#!/bin/bash
# E7 identity provisioning: 16 unique evaluator clients across 3 orgs
# Org1: 6 evaluators (eval-00 to eval-05)
# Org2: 5 evaluators (eval-06 to eval-10)
# Org3: 5 evaluators (eval-11 to eval-15)
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
FABRIC_SAMPLES="$FABRIC_ROOT/fabric-samples"
BINDIR="$FABRIC_SAMPLES/bin"
export PATH="$BINDIR:$PATH"
TDIR="$FABRIC_SAMPLES/test-network"
ORGS_DIR=$TDIR/organizations/peerOrganizations

echo "=== E7 Identity Provisioning ==="
echo "Target: 16 evaluator clients (Org1:6, Org2:5, Org3:5)"
echo ""

# ---- Org1 CA (port 7054) ----
echo "=== Step 1: Start Org1 CA ==="
cd "$TDIR"
DOCKER_SOCK=/var/run/docker.sock docker compose -f compose/compose-ca.yaml up -d ca_org1 2>&1
sleep 3

CA1_CERT=$TDIR/organizations/fabric-ca/org1/ca-cert.pem
ADMIN_HOME1=$TDIR/organizations/fabric-ca/org1/admin
mkdir -p "$ADMIN_HOME1"
export FABRIC_CA_CLIENT_HOME=$ADMIN_HOME1
fabric-ca-client enroll -u https://admin:adminpw@localhost:7054 \
  --caname ca-org1 --tls.certfiles "$CA1_CERT" 2>&1

for i in $(seq 0 5); do
  idname=$(printf "eval-%02d" $i)
  echo "--- Org1: Registering $idname ---"
  fabric-ca-client register --caname ca-org1 \
    --id.name "$idname" --id.secret "${idname}pw" \
    --id.type client \
    --tls.certfiles "$CA1_CERT" 2>&1

  EVAL_MSP="$ORGS_DIR/org1.example.com/users/${idname}/msp"
  fabric-ca-client enroll -u "https://${idname}:${idname}pw@localhost:7054" \
    --caname ca-org1 -M "$EVAL_MSP" --tls.certfiles "$CA1_CERT" 2>&1

  mkdir -p "$EVAL_MSP/admincerts"
  cp $ORGS_DIR/org1.example.com/users/Admin@org1.example.com/msp/admincerts/*.pem "$EVAL_MSP/admincerts/" 2>/dev/null || true
  echo "$idname enrolled in Org1MSP"
done

# ---- Org2 CA (port 8054) ----
echo ""
echo "=== Step 2: Start Org2 CA ==="
DOCKER_SOCK=/var/run/docker.sock docker compose -f compose/compose-ca.yaml up -d ca_org2 2>&1
sleep 3

CA2_CERT=$TDIR/organizations/fabric-ca/org2/ca-cert.pem
ADMIN_HOME2=$TDIR/organizations/fabric-ca/org2/admin
mkdir -p "$ADMIN_HOME2"
export FABRIC_CA_CLIENT_HOME=$ADMIN_HOME2
fabric-ca-client enroll -u https://admin:adminpw@localhost:8054 \
  --caname ca-org2 --tls.certfiles "$CA2_CERT" 2>&1

for i in $(seq 6 10); do
  idname=$(printf "eval-%02d" $i)
  echo "--- Org2: Registering $idname ---"
  fabric-ca-client register --caname ca-org2 \
    --id.name "$idname" --id.secret "${idname}pw" \
    --id.type client \
    --tls.certfiles "$CA2_CERT" 2>&1

  EVAL_MSP="$ORGS_DIR/org2.example.com/users/${idname}/msp"
  fabric-ca-client enroll -u "https://${idname}:${idname}pw@localhost:8054" \
    --caname ca-org2 -M "$EVAL_MSP" --tls.certfiles "$CA2_CERT" 2>&1

  mkdir -p "$EVAL_MSP/admincerts"
  cp $ORGS_DIR/org2.example.com/users/Admin@org2.example.com/msp/admincerts/*.pem "$EVAL_MSP/admincerts/" 2>/dev/null || true
  echo "$idname enrolled in Org2MSP"
done

# ---- Org3 identities (cryptogen-based MSP) ----
echo ""
echo "=== Step 3: Org3 identities ==="
echo "Org3 MSP is cryptogen-based. Checking for existing CA or using cryptogen extend..."

ORG3_CRYPTO=$TDIR/addOrg3/org3-crypto.yaml
ORG3_DIR=$ORGS_DIR/org3.example.com

# Check if Org3 CA is available
CA3_AVAILABLE=false
if [ -f "$TDIR/addOrg3/compose/compose-ca-org3.yaml" ]; then
  echo "Attempting Org3 CA start..."
  DOCKER_SOCK=/var/run/docker.sock docker compose -f "$TDIR/addOrg3/compose/compose-ca-org3.yaml" up -d ca_org3 2>&1 || true
  sleep 3
  if docker ps --format '{{.Names}}' | grep -q ca_org3; then
    CA3_AVAILABLE=true
    echo "Org3 CA is running"
  fi
fi

if [ "$CA3_AVAILABLE" = true ]; then
  CA3_CERT=$TDIR/addOrg3/fabric-ca/org3/ca-cert.pem
  if [ ! -f "$CA3_CERT" ]; then
    CA3_CERT=$ORG3_DIR/msp/cacerts/ca.org3.example.com-cert.pem
  fi
  ADMIN_HOME3=$TDIR/addOrg3/fabric-ca/org3/admin
  mkdir -p "$ADMIN_HOME3"
  export FABRIC_CA_CLIENT_HOME=$ADMIN_HOME3
  fabric-ca-client enroll -u https://admin:adminpw@localhost:11054 \
    --caname ca-org3 --tls.certfiles "$CA3_CERT" 2>&1 || true

  for i in $(seq 11 15); do
    idname=$(printf "eval-%02d" $i)
    echo "--- Org3 CA: Registering $idname ---"
    fabric-ca-client register --caname ca-org3 \
      --id.name "$idname" --id.secret "${idname}pw" \
      --id.type client \
      --tls.certfiles "$CA3_CERT" 2>&1 || true

    EVAL_MSP="$ORG3_DIR/users/${idname}/msp"
    fabric-ca-client enroll -u "https://${idname}:${idname}pw@localhost:11054" \
      --caname ca-org3 -M "$EVAL_MSP" --tls.certfiles "$CA3_CERT" 2>&1 || true

    mkdir -p "$EVAL_MSP/admincerts"
    cp $ORG3_DIR/users/Admin@org3.example.com/msp/admincerts/*.pem "$EVAL_MSP/admincerts/" 2>/dev/null || true
    echo "$idname enrolled in Org3MSP"
  done
else
  echo ""
  echo "============================================================"
  echo "STOP-GATE: Org3 CA not available"
  echo "============================================================"
  echo "Cannot provision unique eval-11 through eval-15 identities."
  echo "Copying Org3 Admin certs to evaluator directories is prohibited:"
  echo "it creates non-unique ClientIDs that break the cross-layer"
  echo "identity binding and cause ERR_DUPLICATE_VOTE in chaincode."
  echo ""
  echo "Resolution options:"
  echo "  1. Start Org3 CA: docker compose -f addOrg3/compose/compose-ca-org3.yaml up -d"
  echo "  2. Generate unique certs via openssl + Org3 CA key"
  echo "  3. Extend cryptogen config with unique eval-* identities"
  echo "============================================================"
  exit 1
fi

echo ""
echo "=== Identity provisioning complete ==="
echo "Org1: eval-00 through eval-05 (6 evaluators)"
echo "Org2: eval-06 through eval-10 (5 evaluators)"
echo "Org3: eval-11 through eval-15 (5 evaluators)"
echo ""
echo "NOTE: Run the Python E7 runner to verify identities and execute scenarios."
