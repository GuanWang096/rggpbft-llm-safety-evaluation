package contracts

import (
	"encoding/json"
	"fmt"

	"github.com/hyperledger/fabric-chaincode-go/v2/pkg/cid"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"google.golang.org/protobuf/types/known/timestamppb"

	"zte-sci.local/trust-evidence/chaincode/core"
)

type stateStub interface {
	GetState(string) ([]byte, error)
	PutState(string, []byte) error
	DelState(string) error
	GetTxTimestamp() (*timestamppb.Timestamp, error)
}

type fabricTx struct {
	stub      stateStub
	caller    core.Caller
	timestamp int64
}

func newFabricTx(stub stateStub, identity cid.ClientIdentity, attributeNames []string) (*fabricTx, error) {
	clientID, err := identity.GetID()
	if err != nil {
		return nil, fmt.Errorf("get client ID: %w", err)
	}
	mspID, err := identity.GetMSPID()
	if err != nil {
		return nil, fmt.Errorf("get MSP ID: %w", err)
	}
	attributes := make(map[string]string)
	for _, name := range attributeNames {
		value, found, err := identity.GetAttributeValue(name)
		if err != nil {
			return nil, fmt.Errorf("get identity attribute %s: %w", name, err)
		}
		if found {
			attributes[name] = value
		}
	}
	timestamp, err := stub.GetTxTimestamp()
	if err != nil {
		return nil, fmt.Errorf("get transaction timestamp: %w", err)
	}
	if timestamp == nil || !timestamp.IsValid() {
		return nil, fmt.Errorf("invalid transaction timestamp")
	}
	return &fabricTx{
		stub: stub,
		caller: core.Caller{
			ClientID:   clientID,
			MSPID:      mspID,
			Attributes: attributes,
		},
		timestamp: timestamp.Seconds,
	}, nil
}

func newFabricTxFromContext(ctx contractapi.TransactionContextInterface) (*fabricTx, error) {
	return newFabricTx(ctx.GetStub(), ctx.GetClientIdentity(), []string{"role"})
}

func (tx *fabricTx) Get(key string, out any) (bool, error) {
	value, err := tx.stub.GetState(key)
	if err != nil {
		return false, err
	}
	if value == nil {
		return false, nil
	}
	if err := json.Unmarshal(value, out); err != nil {
		return false, fmt.Errorf("decode state %s: %w", key, err)
	}
	return true, nil
}

func (tx *fabricTx) Put(key string, value any) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("encode state %s: %w", key, err)
	}
	return tx.stub.PutState(key, encoded)
}

func (tx *fabricTx) Delete(key string) error { return tx.stub.DelState(key) }

func (tx *fabricTx) Caller() core.Caller { return tx.caller }

func (tx *fabricTx) TimestampUnix() int64 { return tx.timestamp }
