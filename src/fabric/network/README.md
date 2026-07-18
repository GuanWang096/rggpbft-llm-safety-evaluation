# Fabric Network

This directory contains the reproducible Fabric/IPFS test network for the paper experiments.

The target topology is three organizations, two peers per organization, three Raft orderers, one CouchDB instance per peer, one application channel, and one Go chaincode package exposing TCMC, AAC, RSRC, and RSAC logical contracts.

The implementation is an independent reconstruction. It does not reproduce or claim identity with the unavailable historical prototype.

Exact component versions and the upstream Fabric sample commit are recorded in `../versions.env`. Container image repository digests are recorded in `../IMAGE_DIGESTS.txt`.

The Fabric 2.5.16 installer did not find a matching `fabric-samples` tag and therefore checked out the upstream `main` branch. The exact commit is pinned so that the bootstrap source remains auditable.
