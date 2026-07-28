#!/usr/bin/env bash
set -euo pipefail

ACTION=${1:-status}
CORE_CONTAINERS=(
  couchdb0
  couchdb1
  orderer.example.com
  peer0.org1.example.com
  peer0.org2.example.com
)

case "$ACTION" in
  start)
    docker start "${CORE_CONTAINERS[@]}" >/dev/null
    docker start ipfs-kubo >/dev/null
    sleep 8
    docker ps --format '{{.Names}} {{.Status}}' |
      grep -E '^(couchdb[01]|orderer\.example\.com|peer0\.org[12]\.example\.com|ipfs-kubo) '
    ;;
  stop)
    docker stop "${CORE_CONTAINERS[@]}" >/dev/null || true
    docker stop ipfs-kubo >/dev/null || true
    ;;
  status)
    docker ps --format '{{.Names}} {{.Status}}' |
      grep -E '^(couchdb[01]|orderer\.example\.com|peer0\.org[12]\.example\.com|ipfs-kubo) ' ||
      true
    ;;
  *)
    echo "Usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac
