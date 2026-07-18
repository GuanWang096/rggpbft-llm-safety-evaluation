package contracts

import (
	"encoding/json"
	"fmt"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"

	"zte-sci.local/trust-evidence/chaincode/core"
)

type TrustEvidenceContract struct {
	contractapi.Contract
}

func (c *TrustEvidenceContract) RegisterEvaluator(ctx contractapi.TransactionContextInterface, inputJSON string) error {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return err
	}
	var input core.RegisterEvaluatorInput
	if err := decodeInput(inputJSON, &input); err != nil {
		return err
	}
	return (core.Service{}).RegisterEvaluator(tx, input)
}

func (c *TrustEvidenceContract) PostTaskConstraint(ctx contractapi.TransactionContextInterface, inputJSON string) error {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return err
	}
	var input core.PostTaskInput
	if err := decodeInput(inputJSON, &input); err != nil {
		return err
	}
	return (core.Service{}).PostTaskConstraint(tx, input)
}

func (c *TrustEvidenceContract) QueryTask(ctx contractapi.TransactionContextInterface, taskID string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).QueryTask(tx, taskID)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) QueryAllocation(ctx contractapi.TransactionContextInterface, taskID string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).QueryAllocation(tx, taskID)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) QueryConfirmation(ctx contractapi.TransactionContextInterface, taskID string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).QueryConfirmation(tx, taskID)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) QueryEvaluator(ctx contractapi.TransactionContextInterface, evalID string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).QueryEvaluator(tx, evalID)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) QueryTaskReputation(ctx contractapi.TransactionContextInterface, subjectID, taskID string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).QueryTaskReputation(tx, subjectID, taskID)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) PostAllocation(ctx contractapi.TransactionContextInterface, inputJSON string) error {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return err
	}
	var input core.PostAllocationInput
	if err := decodeInput(inputJSON, &input); err != nil {
		return err
	}
	return (core.Service{}).PostAllocation(tx, input)
}

func (c *TrustEvidenceContract) PostEvalSnapshot(ctx contractapi.TransactionContextInterface, inputJSON string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	var input core.SnapshotInput
	if err := decodeInput(inputJSON, &input); err != nil {
		return "", err
	}
	result, err := (core.Service{}).PostEvalSnapshot(tx, input)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) SubmitVote(ctx contractapi.TransactionContextInterface, taskID, digest, voteType string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).SubmitVote(tx, taskID, digest, voteType)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) FinalizeConfirmation(ctx contractapi.TransactionContextInterface, taskID string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).FinalizeConfirmation(tx, taskID)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) PostReviewDecision(ctx contractapi.TransactionContextInterface, taskID, decision string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).PostReviewDecision(tx, taskID, decision)
	return encodeResult(result, err)
}

func (c *TrustEvidenceContract) ProcessSettlement(ctx contractapi.TransactionContextInterface, taskID string) (string, error) {
	tx, err := newFabricTxFromContext(ctx)
	if err != nil {
		return "", err
	}
	result, err := (core.Service{}).ProcessSettlement(tx, taskID)
	return encodeResult(result, err)
}

func decodeInput(inputJSON string, target any) error {
	if err := json.Unmarshal([]byte(inputJSON), target); err != nil {
		return core.CodeError{Code: "ERR_INVALID_JSON", Message: err.Error()}
	}
	return nil
}

func encodeResult(value any, callErr error) (string, error) {
	if callErr != nil {
		return "", callErr
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", fmt.Errorf("encode result: %w", err)
	}
	return string(encoded), nil
}
