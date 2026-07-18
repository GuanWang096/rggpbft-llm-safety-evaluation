#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
TDIR="$FABRIC_ROOT/fabric-samples/test-network"
VERSION=${1:-1.1}
SEQUENCE=${2:-2}
LABEL="tce_${VERSION}"
PACKAGE="$TDIR/${LABEL}.tar.gz"

export PATH="$FABRIC_ROOT/fabric-samples/bin:$PATH"
export FABRIC_CFG_PATH="$FABRIC_ROOT/fabric-samples/config"
export CORE_PEER_TLS_ENABLED=true
export GOFLAGS="${GOFLAGS:-} -buildvcs=false"

ORDERER_CA="$TDIR/organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"
PEER1_TLS="$TDIR/organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem"
PEER2_TLS="$TDIR/organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem"
PEER3_TLS="$TDIR/organizations/peerOrganizations/org3.example.com/tlsca/tlsca.org3.example.com-cert.pem"

set_org() {
    local org=$1
    local port=$2
    export CORE_PEER_LOCALMSPID="${org^}MSP"
    export CORE_PEER_TLS_ROOTCERT_FILE="$TDIR/organizations/peerOrganizations/${org}.example.com/peers/peer0.${org}.example.com/tls/ca.crt"
    export CORE_PEER_MSPCONFIGPATH="$TDIR/organizations/peerOrganizations/${org}.example.com/users/Admin@${org}.example.com/msp"
    export CORE_PEER_ADDRESS="localhost:${port}"
}

cd "$TDIR"
set_org org1 7051
committed=$(peer lifecycle chaincode querycommitted --channelID trustchannel --name tce 2>/dev/null || true)
if grep -q "Version: $VERSION, Sequence: $SEQUENCE" <<<"$committed"; then
    echo "Chaincode tce $VERSION sequence $SEQUENCE is already committed"
    exit 0
fi
peer lifecycle chaincode package "$PACKAGE" --path "$FABRIC_ROOT/chaincode" --lang golang --label "$LABEL"

for spec in "org1 7051" "org2 9051" "org3 11051"; do
    read -r org port <<<"$spec"
    set_org "$org" "$port"
    peer lifecycle chaincode install "$PACKAGE" >/tmp/tce-install.log 2>&1 || {
        if ! grep -q "already successfully installed" /tmp/tce-install.log; then
            cat /tmp/tce-install.log
            exit 1
        fi
    }
done

set_org org1 7051
PACKAGE_ID=$(peer lifecycle chaincode queryinstalled | awk -v label="$LABEL" '$0 ~ "Label: " label {sub(/^Package ID: /, ""); sub(/, Label:.*/, ""); print; exit}')
if [[ -z "$PACKAGE_ID" ]]; then
    echo "Unable to resolve package ID for $LABEL" >&2
    exit 1
fi

for spec in "org1 7051" "org2 9051" "org3 11051"; do
    read -r org port <<<"$spec"
    set_org "$org" "$port"
    peer lifecycle chaincode approveformyorg \
        -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
        --tls --cafile "$ORDERER_CA" --channelID trustchannel --name tce \
        --version "$VERSION" --package-id "$PACKAGE_ID" --sequence "$SEQUENCE"
done

set_org org1 7051
peer lifecycle chaincode checkcommitreadiness --channelID trustchannel --name tce \
    --version "$VERSION" --sequence "$SEQUENCE" --output json
peer lifecycle chaincode commit \
    -o localhost:7050 --ordererTLSHostnameOverride orderer.example.com \
    --tls --cafile "$ORDERER_CA" --channelID trustchannel --name tce \
    --version "$VERSION" --sequence "$SEQUENCE" \
    --peerAddresses localhost:7051 --tlsRootCertFiles "$PEER1_TLS" \
    --peerAddresses localhost:9051 --tlsRootCertFiles "$PEER2_TLS" \
    --peerAddresses localhost:11051 --tlsRootCertFiles "$PEER3_TLS"

peer lifecycle chaincode querycommitted --channelID trustchannel --name tce
