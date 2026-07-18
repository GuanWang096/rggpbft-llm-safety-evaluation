#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FABRIC_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
FABRIC_SAMPLES="$FABRIC_ROOT/fabric-samples"
BINDIR="$FABRIC_SAMPLES/bin"
export PATH="$BINDIR:$PATH"
export FABRIC_CFG_PATH="$FABRIC_SAMPLES/config"
cd "$FABRIC_SAMPLES/test-network/addOrg3"

echo "=== Generate Org3 crypto ==="
./addOrg3.sh generate 2>&1

echo ""
echo "=== Start Org3 containers ==="
./addOrg3.sh up -c trustchannel -s couchdb 2>&1
