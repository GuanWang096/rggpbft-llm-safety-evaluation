package contracts

import (
	"crypto/x509"
	"encoding/json"
	"errors"
	"reflect"
	"testing"

	"google.golang.org/protobuf/types/known/timestamppb"

	"zte-sci.local/trust-evidence/chaincode/core"
)

type fakeStateStub struct {
	state     map[string][]byte
	timestamp *timestamppb.Timestamp
}

func (stub *fakeStateStub) GetState(key string) ([]byte, error) {
	return stub.state[key], nil
}

func (stub *fakeStateStub) PutState(key string, value []byte) error {
	stub.state[key] = append([]byte(nil), value...)
	return nil
}

func (stub *fakeStateStub) DelState(key string) error {
	delete(stub.state, key)
	return nil
}

func (stub *fakeStateStub) GetTxTimestamp() (*timestamppb.Timestamp, error) {
	return stub.timestamp, nil
}

type fakeIdentity struct {
	id         string
	mspID      string
	attributes map[string]string
}

func (identity fakeIdentity) GetID() (string, error)                         { return identity.id, nil }
func (identity fakeIdentity) GetMSPID() (string, error)                      { return identity.mspID, nil }
func (identity fakeIdentity) GetX509Certificate() (*x509.Certificate, error) { return nil, nil }
func (identity fakeIdentity) GetAttributeValue(name string) (string, bool, error) {
	value, found := identity.attributes[name]
	return value, found, nil
}
func (identity fakeIdentity) AssertAttributeValue(name, value string) error {
	actual, found := identity.attributes[name]
	if !found || actual != value {
		return errors.New("attribute mismatch")
	}
	return nil
}

func TestFabricTransactionAdapter(t *testing.T) {
	stub := &fakeStateStub{state: map[string][]byte{}, timestamp: timestamppb.New(timestamppb.Now().AsTime())}
	stub.timestamp.Seconds = 1_700_000_123
	identity := fakeIdentity{id: "client-1", mspID: "Org1MSP", attributes: map[string]string{"role": "audit_service"}}
	tx, err := newFabricTx(stub, identity, []string{"role", "missing"})
	if err != nil {
		t.Fatalf("newFabricTx() error = %v", err)
	}

	wantCaller := core.Caller{ClientID: "client-1", MSPID: "Org1MSP", Attributes: map[string]string{"role": "audit_service"}}
	if got := tx.Caller(); !reflect.DeepEqual(got, wantCaller) {
		t.Fatalf("Caller() = %#v, want %#v", got, wantCaller)
	}
	if got := tx.TimestampUnix(); got != stub.timestamp.Seconds {
		t.Fatalf("TimestampUnix() = %d", got)
	}

	want := core.Task{TaskID: "task-1", SubjectID: "model-1"}
	if err := tx.Put(core.TaskKey(want.TaskID), want); err != nil {
		t.Fatal(err)
	}
	var got core.Task
	found, err := tx.Get(core.TaskKey(want.TaskID), &got)
	if err != nil || !found || !reflect.DeepEqual(got, want) {
		t.Fatalf("Get() found=%v error=%v value=%#v", found, err, got)
	}
	if !json.Valid(stub.state[core.TaskKey(want.TaskID)]) {
		t.Fatalf("stored state is not JSON")
	}
	if err := tx.Delete(core.TaskKey(want.TaskID)); err != nil {
		t.Fatal(err)
	}
	found, err = tx.Get(core.TaskKey(want.TaskID), &got)
	if err != nil || found {
		t.Fatalf("deleted state found=%v error=%v", found, err)
	}
}
