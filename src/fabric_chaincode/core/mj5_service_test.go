package core

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
)

type mj5FakeTx struct {
	state  map[string][]byte
	caller Caller
	now    int64
}

func (tx *mj5FakeTx) Get(key string, out any) (bool, error) {
	value, found := tx.state[key]
	if !found {
		return false, nil
	}
	return true, json.Unmarshal(value, out)
}

func (tx *mj5FakeTx) Put(key string, value any) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	tx.state[key] = encoded
	return nil
}

func (tx *mj5FakeTx) Delete(key string) error {
	delete(tx.state, key)
	return nil
}

func (tx *mj5FakeTx) Caller() Caller       { return tx.caller }
func (tx *mj5FakeTx) TimestampUnix() int64 { return tx.now }

type mj5Fixture struct {
	tx             *mj5FakeTx
	service        Service
	judgePrivate   map[string]ed25519.PrivateKey
	validatorPriv  map[string]ed25519.PrivateKey
	evidenceSHA256 string
	policySHA256   string
}

func newMJ5Fixture(t *testing.T) *mj5Fixture {
	t.Helper()
	fixture := &mj5Fixture{
		tx: &mj5FakeTx{
			state: map[string][]byte{},
			caller: Caller{
				ClientID: "audit-client",
				MSPID:    "Org1MSP",
				Attributes: map[string]string{
					"role": "audit_service",
				},
			},
			now: 1_700_000_000,
		},
		judgePrivate:   map[string]ed25519.PrivateKey{},
		validatorPriv:  map[string]ed25519.PrivateKey{},
		evidenceSHA256: strings.Repeat("a", 64),
		policySHA256:   strings.Repeat("b", 64),
	}
	for _, judgeID := range []string{"qwen", "safework", "minicpm"} {
		publicKey, privateKey := deterministicKey("judge-" + judgeID)
		fixture.judgePrivate[judgeID] = privateKey
		err := fixture.service.RegisterJudge(fixture.tx, RegisterJudgeInput{
			JudgeID: judgeID, Organization: "test-org", ModelID: judgeID + "-model",
			ModelRevision: "revision-1", PolicySHA256: fixture.policySHA256,
			AdapterVersion: judgeID + "-adapter-v1",
			PublicKeyHex:   hex.EncodeToString(publicKey),
		})
		if err != nil {
			t.Fatalf("register judge %s: %v", judgeID, err)
		}
	}
	for nodeID := 0; nodeID < 5; nodeID++ {
		validatorID := fmt.Sprintf("validator-%02d", nodeID)
		publicKey, privateKey := rggValidatorKey(nodeID)
		fixture.validatorPriv[validatorID] = privateKey
		err := fixture.service.RegisterValidator(fixture.tx, RegisterValidatorInput{
			ValidatorID: validatorID, PublicKeyHex: hex.EncodeToString(publicKey),
			ReliabilityPPM: 900_000, Version: 1,
		})
		if err != nil {
			t.Fatalf("register validator %s: %v", validatorID, err)
		}
	}
	return fixture
}

func deterministicKey(material string) (ed25519.PublicKey, ed25519.PrivateKey) {
	seed := sha256.Sum256([]byte(material))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	return privateKey.Public().(ed25519.PublicKey), privateKey
}

func rggValidatorKey(nodeID int) (ed25519.PublicKey, ed25519.PrivateKey) {
	seed := sha256.Sum256([]byte(fmt.Sprintf("zte-sci-rggpbft-node-%d", nodeID)))
	privateKey := ed25519.NewKeyFromSeed(seed[:])
	return privateKey.Public().(ed25519.PublicKey), privateKey
}

func leaderValidatorIDs() []string {
	return []string{"validator-00", "validator-01", "validator-02", "validator-03"}
}

func (fixture *mj5Fixture) freezeInput(decisionID string) FreezeDecisionInput {
	labels := map[string]string{
		"qwen": "safe", "safework": "unsafe", "minicpm": "unsafe",
	}
	outputs := make([]SignedJudgeOutput, 0, len(labels))
	for _, judgeID := range []string{"qwen", "safework", "minicpm"} {
		output := SignedJudgeOutput{
			JudgeID: judgeID, DecisionID: decisionID, SampleID: "sample-001",
			Label: labels[judgeID], EvidenceSHA256: fixture.evidenceSHA256,
			PolicySHA256:   fixture.policySHA256,
			AdapterVersion: judgeID + "-adapter-v1",
		}
		output.SignatureHex = hex.EncodeToString(
			ed25519.Sign(fixture.judgePrivate[judgeID], CanonicalJudgeOutput(output)),
		)
		outputs = append(outputs, output)
	}
	return FreezeDecisionInput{
		DecisionID: decisionID, SampleID: "sample-001",
		EvidenceCID: "bafy-test-evidence", EvidenceSHA256: fixture.evidenceSHA256,
		JudgeOutputs: outputs, ProvisionalLabel: "unsafe",
		PosteriorUnsafePPM: 800_000, CommitteeQuorum: 2,
		CertificateQuorum: 3, LeaderValidatorIDs: leaderValidatorIDs(),
		DeadlineUnix: fixture.tx.now + 60,
	}
}

func (fixture *mj5Fixture) signedVote(
	snapshot DecisionSnapshot,
	validatorID, voteType string,
) SubmitCommitteeVoteInput {
	input := SubmitCommitteeVoteInput{
		DecisionID: snapshot.DecisionID, DecisionDigest: snapshot.DecisionDigest,
		ValidatorID: validatorID, VoteType: voteType, ValidatorVersion: 1,
	}
	input.SignatureHex = hex.EncodeToString(
		ed25519.Sign(fixture.validatorPriv[validatorID], CanonicalCommitteeVote(input)),
	)
	return input
}

func (fixture *mj5Fixture) certificateInput(snapshot DecisionSnapshot) CertifyDecisionInput {
	input := CertifyDecisionInput{
		DecisionID: snapshot.DecisionID, DecisionDigest: snapshot.DecisionDigest,
		View: 2, Sequence: 7,
	}
	for nodeID := 0; nodeID < 3; nodeID++ {
		input.ProtocolMessages = append(
			input.ProtocolMessages,
			fixture.protocolMessage(nodeID, input.View, input.Sequence, input.DecisionDigest),
		)
	}
	return input
}

func (fixture *mj5Fixture) protocolMessage(
	nodeID int,
	view, sequence int64,
	digest string,
) RGGProtocolMessage {
	message := RGGProtocolMessage{
		Type:     "GLOBAL_COMMIT",
		Sender:   int64(nodeID),
		View:     view,
		Sequence: sequence,
		Digest:   digest,
		Group:    -1,
		Payload:  json.RawMessage(`{"start_ns":1700000000000000001}`),
	}
	canonical, err := CanonicalRGGProtocolMessage(message)
	if err != nil {
		panic(err)
	}
	validatorID := fmt.Sprintf("validator-%02d", nodeID)
	message.Signature = base64.StdEncoding.EncodeToString(
		ed25519.Sign(fixture.validatorPriv[validatorID], canonical),
	)
	return message
}

func (fixture *mj5Fixture) batchInput(decisionID string) FreezeDecisionBatchInput {
	records := make([]DecisionRecord, 0, 2)
	for index, sampleID := range []string{"sample-001", "sample-002"} {
		evidence := sha256.Sum256([]byte(sampleID))
		evidenceHex := hex.EncodeToString(evidence[:])
		labels := map[string]string{
			"qwen": "safe", "safework": "unsafe", "minicpm": "unsafe",
		}
		outputs := make([]SignedJudgeOutput, 0, len(labels))
		for _, judgeID := range []string{"qwen", "safework", "minicpm"} {
			output := SignedJudgeOutput{
				JudgeID: judgeID, DecisionID: decisionID, SampleID: sampleID,
				Label: labels[judgeID], EvidenceSHA256: evidenceHex,
				PolicySHA256:   fixture.policySHA256,
				AdapterVersion: judgeID + "-adapter-v1",
			}
			output.SignatureHex = hex.EncodeToString(
				ed25519.Sign(fixture.judgePrivate[judgeID], CanonicalJudgeOutput(output)),
			)
			outputs = append(outputs, output)
		}
		records = append(records, DecisionRecord{
			SampleID: sampleID, EvidenceSHA256: evidenceHex,
			JudgeOutputs: outputs, ProvisionalLabel: "unsafe",
			PosteriorUnsafePPM: int64(700_000 + index*10_000),
		})
	}
	return FreezeDecisionBatchInput{
		DecisionID: decisionID, EvidenceCID: "bafy-batch",
		EvidenceSHA256: fixture.evidenceSHA256, Records: records,
		CommitteeQuorum: 2, CertificateQuorum: 3,
		LeaderValidatorIDs: leaderValidatorIDs(),
		DeadlineUnix:       fixture.tx.now + 60,
	}
}

func requireMJ5Code(t *testing.T, err error, code string) {
	t.Helper()
	var coded CodeError
	if !errors.As(err, &coded) || coded.Code != code {
		t.Fatalf("error = %v, want code %s", err, code)
	}
}

func TestMJ5LifecycleAndPredictBeforeUpdate(t *testing.T) {
	fixture := newMJ5Fixture(t)
	snapshot, err := fixture.service.FreezeDecisionSnapshot(fixture.tx, fixture.freezeInput("decision-main"))
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Status != DecisionStatusSnapshotFrozen || len(snapshot.FrozenReliabilities) != 3 {
		t.Fatalf("unexpected frozen snapshot: %#v", snapshot)
	}
	for _, frozen := range snapshot.FrozenReliabilities {
		if frozen.GUnsafePPM != 500_000 || frozen.GSafePPM != 500_000 || frozen.Version != 1 {
			t.Fatalf("unexpected frozen reliability: %#v", frozen)
		}
	}

	for _, validatorID := range []string{"validator-00", "validator-01"} {
		snapshot, err = fixture.service.SubmitCommitteeVote(
			fixture.tx, fixture.signedVote(snapshot, validatorID, "ACK"),
		)
		if err != nil {
			t.Fatal(err)
		}
	}
	if snapshot.Status != DecisionStatusCommitteeConfirmed {
		t.Fatalf("status = %s", snapshot.Status)
	}
	snapshot, err = fixture.service.CertifyDecision(fixture.tx, fixture.certificateInput(snapshot))
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Status != DecisionStatusCertified || snapshot.Certificate == nil ||
		!snapshot.Certificate.Verified || len(snapshot.Certificate.Signers) != 3 ||
		len(snapshot.Certificate.ProtocolMessages) != 3 ||
		snapshot.Certificate.ProtocolCertificateSHA == "" {
		t.Fatalf("unexpected certified snapshot: %#v", snapshot)
	}

	snapshot, err = fixture.service.SettleDecision(fixture.tx, SettleDecisionInput{
		DecisionID: snapshot.DecisionID, IndependentLabel: "unsafe",
	})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Status != DecisionStatusSettled || !snapshot.Settled {
		t.Fatalf("unexpected settlement: %#v", snapshot)
	}
	qwen, err := fixture.service.QueryJudge(fixture.tx, "qwen")
	if err != nil {
		t.Fatal(err)
	}
	safework, err := fixture.service.QueryJudge(fixture.tx, "safework")
	if err != nil {
		t.Fatal(err)
	}
	if qwen.GUnsafePPM >= 500_000 || safework.GUnsafePPM <= 500_000 {
		t.Fatalf("class-conditional update is not monotone: qwen=%d safework=%d", qwen.GUnsafePPM, safework.GUnsafePPM)
	}
	for _, frozen := range snapshot.FrozenReliabilities {
		if frozen.GUnsafePPM != 500_000 || frozen.Version != 1 {
			t.Fatalf("settlement rewrote frozen reliability: %#v", frozen)
		}
	}
	_, err = fixture.service.SettleDecision(fixture.tx, SettleDecisionInput{
		DecisionID: snapshot.DecisionID, IndependentLabel: "unsafe",
	})
	requireMJ5Code(t, err, "ERR_ALREADY_SETTLED")
}

func TestMJ5JudgePayloadNegativeCases(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*mj5Fixture, *FreezeDecisionInput)
		code   string
	}{
		{
			name: "label modification",
			mutate: func(_ *mj5Fixture, input *FreezeDecisionInput) {
				input.JudgeOutputs[0].Label = "unsafe"
			},
			code: "ERR_JUDGE_SIGNATURE",
		},
		{
			name: "service identity mismatch",
			mutate: func(_ *mj5Fixture, input *FreezeDecisionInput) {
				input.JudgeOutputs[0].JudgeID = "safework"
			},
			code: "ERR_ADAPTER_VERSION_MISMATCH",
		},
		{
			name: "policy hash mismatch",
			mutate: func(_ *mj5Fixture, input *FreezeDecisionInput) {
				input.JudgeOutputs[0].PolicySHA256 = strings.Repeat("c", 64)
			},
			code: "ERR_POLICY_HASH_MISMATCH",
		},
		{
			name: "evidence substitution",
			mutate: func(_ *mj5Fixture, input *FreezeDecisionInput) {
				input.JudgeOutputs[0].EvidenceSHA256 = strings.Repeat("d", 64)
			},
			code: "ERR_JUDGE_PAYLOAD_BINDING",
		},
		{
			name: "decision replay",
			mutate: func(_ *mj5Fixture, input *FreezeDecisionInput) {
				input.JudgeOutputs[0].DecisionID = "old-decision"
			},
			code: "ERR_JUDGE_PAYLOAD_BINDING",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := newMJ5Fixture(t)
			input := fixture.freezeInput("decision-negative")
			test.mutate(fixture, &input)
			_, err := fixture.service.FreezeDecisionSnapshot(fixture.tx, input)
			requireMJ5Code(t, err, test.code)
		})
	}
}

func TestMJ5ReviewAndTimeoutPaths(t *testing.T) {
	t.Run("object routes to review", func(t *testing.T) {
		fixture := newMJ5Fixture(t)
		snapshot, err := fixture.service.FreezeDecisionSnapshot(fixture.tx, fixture.freezeInput("decision-object"))
		if err != nil {
			t.Fatal(err)
		}
		snapshot, err = fixture.service.SubmitCommitteeVote(
			fixture.tx, fixture.signedVote(snapshot, "validator-00", "OBJECT"),
		)
		if err != nil {
			t.Fatal(err)
		}
		if snapshot.Status != DecisionStatusReview {
			t.Fatalf("status = %s", snapshot.Status)
		}
	})

	t.Run("deadline routes to review", func(t *testing.T) {
		fixture := newMJ5Fixture(t)
		snapshot, err := fixture.service.FreezeDecisionSnapshot(fixture.tx, fixture.freezeInput("decision-timeout"))
		if err != nil {
			t.Fatal(err)
		}
		fixture.tx.now = snapshot.DeadlineUnix + 1
		snapshot, err = fixture.service.FinalizeDecisionTimeout(fixture.tx, snapshot.DecisionID)
		if err != nil {
			t.Fatal(err)
		}
		if snapshot.Status != DecisionStatusReview {
			t.Fatalf("status = %s", snapshot.Status)
		}
	})
}

func TestMJ5VoteAndCertificateBindings(t *testing.T) {
	fixture := newMJ5Fixture(t)
	snapshot, err := fixture.service.FreezeDecisionSnapshot(fixture.tx, fixture.freezeInput("decision-bindings"))
	if err != nil {
		t.Fatal(err)
	}
	vote := fixture.signedVote(snapshot, "validator-00", "ACK")
	if _, err = fixture.service.SubmitCommitteeVote(fixture.tx, vote); err != nil {
		t.Fatal(err)
	}
	_, err = fixture.service.SubmitCommitteeVote(fixture.tx, vote)
	requireMJ5Code(t, err, "ERR_DUPLICATE_VOTE")

	snapshot, err = fixture.service.SubmitCommitteeVote(
		fixture.tx, fixture.signedVote(snapshot, "validator-01", "ACK"),
	)
	if err != nil {
		t.Fatal(err)
	}
	certificate := fixture.certificateInput(snapshot)
	certificate.DecisionDigest = strings.Repeat("e", 64)
	_, err = fixture.service.CertifyDecision(fixture.tx, certificate)
	requireMJ5Code(t, err, "ERR_CERTIFICATE_DIGEST_MISMATCH")

	certificate = fixture.certificateInput(snapshot)
	certificate.ProtocolMessages[0].Signature = base64.StdEncoding.EncodeToString(
		make([]byte, ed25519.SignatureSize),
	)
	_, err = fixture.service.CertifyDecision(fixture.tx, certificate)
	requireMJ5Code(t, err, "ERR_CERTIFICATE_SIGNATURE")

	certificate = fixture.certificateInput(snapshot)
	certificate.ProtocolMessages[2] = fixture.protocolMessage(
		4, certificate.View, certificate.Sequence, certificate.DecisionDigest,
	)
	_, err = fixture.service.CertifyDecision(fixture.tx, certificate)
	requireMJ5Code(t, err, "ERR_CERTIFICATE_SIGNER_NOT_LEADER")

	certificate = fixture.certificateInput(snapshot)
	certificate.ProtocolMessages[2] = certificate.ProtocolMessages[1]
	_, err = fixture.service.CertifyDecision(fixture.tx, certificate)
	requireMJ5Code(t, err, "ERR_DUPLICATE_CERTIFICATE_SIGNER")

	certificate = fixture.certificateInput(snapshot)
	certificate.ProtocolMessages[0].Sequence++
	_, err = fixture.service.CertifyDecision(fixture.tx, certificate)
	requireMJ5Code(t, err, "ERR_CERTIFICATE_TUPLE")
}

func TestMJ5LeaderCommitteeValidation(t *testing.T) {
	fixture := newMJ5Fixture(t)
	input := fixture.freezeInput("decision-invalid-leaders")
	input.LeaderValidatorIDs = []string{
		"validator-00", "validator-01", "validator-02", "validator-02",
	}
	_, err := fixture.service.FreezeDecisionSnapshot(fixture.tx, input)
	requireMJ5Code(t, err, "ERR_INVALID_LEADER_COMMITTEE")

	input = fixture.freezeInput("decision-invalid-quorum")
	input.CertificateQuorum = 2
	_, err = fixture.service.FreezeDecisionSnapshot(fixture.tx, input)
	requireMJ5Code(t, err, "ERR_INVALID_CERTIFICATE_QUORUM")
}

func TestMJ5ProtocolCertificateDigestMatchesPython(t *testing.T) {
	fixture := newMJ5Fixture(t)
	digest := strings.Repeat("c", 64)
	messages := make([]RGGProtocolMessage, 0, 3)
	for nodeID := 0; nodeID < 3; nodeID++ {
		messages = append(
			messages,
			fixture.protocolMessage(nodeID, 2, 7, digest),
		)
	}
	actual, err := ProtocolCertificateDigest(messages)
	if err != nil {
		t.Fatal(err)
	}
	const expected = "fda212f14538a00e0e5e4feb45e4217f75cfab2d74d857db0a7ad470059a80df"
	if actual != expected {
		t.Fatalf("protocol certificate digest = %s, want %s", actual, expected)
	}
}

func TestMJ5BatchLifecycleAndSettlementMembership(t *testing.T) {
	fixture := newMJ5Fixture(t)
	snapshot, err := fixture.service.FreezeDecisionBatch(fixture.tx, fixture.batchInput("batch-main"))
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Records) != 2 || len(snapshot.FrozenReliabilities) != 3 {
		t.Fatalf("unexpected batch snapshot: %#v", snapshot)
	}
	for _, validatorID := range []string{"validator-00", "validator-01"} {
		snapshot, err = fixture.service.SubmitCommitteeVote(
			fixture.tx, fixture.signedVote(snapshot, validatorID, "ACK"),
		)
		if err != nil {
			t.Fatal(err)
		}
	}
	snapshot, err = fixture.service.CertifyDecision(fixture.tx, fixture.certificateInput(snapshot))
	if err != nil {
		t.Fatal(err)
	}
	_, err = fixture.service.SettleDecision(fixture.tx, SettleDecisionInput{
		DecisionID: snapshot.DecisionID,
		IndependentLabels: []SettlementLabel{
			{SampleID: "sample-001", Label: "unsafe"},
		},
	})
	requireMJ5Code(t, err, "ERR_SETTLEMENT_MEMBERSHIP")

	snapshot, err = fixture.service.SettleDecision(fixture.tx, SettleDecisionInput{
		DecisionID: snapshot.DecisionID,
		IndependentLabels: []SettlementLabel{
			{SampleID: "sample-001", Label: "unsafe"},
			{SampleID: "sample-002", Label: "safe"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Status != DecisionStatusSettled || snapshot.SettlementLabel != "batch:2" {
		t.Fatalf("unexpected batch settlement: %#v", snapshot)
	}
}

func TestMJ5BatchRejectsInconsistentJudgeSet(t *testing.T) {
	fixture := newMJ5Fixture(t)
	input := fixture.batchInput("batch-inconsistent")
	input.Records[1].JudgeOutputs = input.Records[1].JudgeOutputs[:2]
	_, err := fixture.service.FreezeDecisionBatch(fixture.tx, input)
	requireMJ5Code(t, err, "ERR_INVALID_DECISION_RECORD")
}
