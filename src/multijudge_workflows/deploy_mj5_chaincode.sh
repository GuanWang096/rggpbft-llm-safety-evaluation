#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
FABRIC_ROOT="$REPO_ROOT/src/fabric"
TDIR="$FABRIC_ROOT/fabric-samples/test-network"
CHAINCODE_PATH="$REPO_ROOT/src/fabric_chaincode"
VERSION=4.1
SEQUENCE=5
LABEL=mj5_4.1
PACKAGE="$TDIR/${LABEL}.tar.gz"

export PATH="$FABRIC_ROOT/fabric-samples/bin:/usr/bin:/bin"
export FABRIC_CFG_PATH="$FABRIC_ROOT/fabric-samples/config"
export CORE_PEER_TLS_ENABLED=true
export GOFLAGS="${GOFLAGS:-} -buildvcs=false"

ORDERER_CA="$TDIR/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"
PEER1_TLS="$TDIR/organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem"
PEER2_TLS="$TDIR/organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem"

set_org() {
  local org=$1
  local port=$2
  local msp
  if [[ "$org" == org1 ]]; then
    msp=Org1MSP
  else
    msp=Org2MSP
  fi
  export CORE_PEER_LOCALMSPID="$msp"
  export CORE_PEER_TLS_ROOTCERT_FILE="$TDIR/organizations/peerOrganizations/${org}.example.com/peers/peer0.${org}.example.com/tls/ca.crt"
  export CORE_PEER_MSPCONFIGPATH="$TDIR/organizations/peerOrganizations/${org}.example.com/users/Admin@${org}.example.com/msp"
  export CORE_PEER_ADDRESS="localhost:${port}"
}

set_org org1 7051
committed=$(peer lifecycle chaincode querycommitted --channelID trustchannel --name tce 2>/dev/null || true)
if grep -q "Version: ${VERSION}, Sequence: ${SEQUENCE}" <<<"$committed"; then
  echo "tce ${VERSION} sequence ${SEQUENCE} is already committed"
  exit 0
fi

rm -f "$PACKAGE"
peer lifecycle chaincode package "$PACKAGE" \
  --path "$CHAINCODE_PATH" --lang golang --label "$LABEL"

for spec in "org1 7051" "org2 9051"; do
  read -r org port <<<"$spec"
  set_org "$org" "$port"
  log_file="/tmp/mj5-install-${org}.log"
  if ! peer lifecycle chaincode install "$PACKAGE" >"$log_file" 2>&1; then
    if ! grep -q "already successfully installed" "$log_file"; then
      cat "$log_file"
      exit 1
    fi
  fi
done

set_org org1 7051
PACKAGE_ID=$(peer lifecycle chaincode queryinstalled |
  awk -v label="$LABEL" '$0 ~ "Label: " label {sub(/^Package ID: /, ""); sub(/, Label:.*/, ""); print; exit}')
test -n "$PACKAGE_ID"

for spec in "org1 7051" "org2 9051"; do
  read -r org port <<<"$spec"
  set_org "$org" "$port"
  peer lifecycle chaincode approveformyorg \
    -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "$ORDERER_CA" --channelID trustchannel --name tce \
    --version "$VERSION" --package-id "$PACKAGE_ID" --sequence "$SEQUENCE"
done

set_org org1 7051
peer lifecycle chaincode checkcommitreadiness \
  --channelID trustchannel --name tce --version "$VERSION" \
  --sequence "$SEQUENCE" --output json
peer lifecycle chaincode commit \
  -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
  --tls --cafile "$ORDERER_CA" --channelID trustchannel --name tce \
  --version "$VERSION" --sequence "$SEQUENCE" \
  --peerAddresses localhost:7051 --tlsRootCertFiles "$PEER1_TLS" \
  --peerAddresses localhost:9051 --tlsRootCertFiles "$PEER2_TLS"
peer lifecycle chaincode querycommitted --channelID trustchannel --name tce
