package main

import (
	"log"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"

	"zte-sci.local/trust-evidence/chaincode/contracts"
)

func main() {
	chaincode, err := contractapi.NewChaincode(&contracts.TrustEvidenceContract{})
	if err != nil {
		log.Panicf("create chaincode: %v", err)
	}
	if err := chaincode.Start(); err != nil {
		log.Panicf("start chaincode: %v", err)
	}
}
