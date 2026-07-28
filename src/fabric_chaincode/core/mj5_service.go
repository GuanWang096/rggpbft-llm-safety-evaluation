package core

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
)

func (Service) RegisterJudge(tx Tx, input RegisterJudgeInput) error {
	if err := requireAuditService(tx); err != nil {
		return err
	}
	if strings.TrimSpace(input.JudgeID) == "" ||
		strings.TrimSpace(input.ModelID) == "" ||
		strings.TrimSpace(input.ModelRevision) == "" ||
		strings.TrimSpace(input.AdapterVersion) == "" {
		return CodeError{Code: "ERR_INVALID_JUDGE_IDENTITY", Message: input.JudgeID}
	}
	if !isSHA256(input.PolicySHA256) {
		return CodeError{Code: "ERR_INVALID_POLICY_HASH", Message: input.JudgeID}
	}
	publicKey, fingerprint, err := decodeEd25519PublicKey(input.PublicKeyHex)
	if err != nil {
		return err
	}
	key := JudgeStateKey(input.JudgeID)
	var existing JudgeState
	found, err := tx.Get(key, &existing)
	if err != nil {
		return fmt.Errorf("read judge: %w", err)
	}
	if found {
		return CodeError{Code: "ERR_JUDGE_EXISTS", Message: input.JudgeID}
	}
	counts := []int64{
		input.UnsafeCorrectMicro,
		input.UnsafeIncorrectMicro,
		input.SafeCorrectMicro,
		input.SafeIncorrectMicro,
	}
	allZero := true
	for _, count := range counts {
		if count < 0 {
			return CodeError{Code: "ERR_INVALID_RELIABILITY_COUNTS", Message: input.JudgeID}
		}
		allZero = allZero && count == 0
	}
	if allZero {
		input.UnsafeCorrectMicro = OnePPM
		input.UnsafeIncorrectMicro = OnePPM
		input.SafeCorrectMicro = OnePPM
		input.SafeIncorrectMicro = OnePPM
	} else if input.UnsafeCorrectMicro == 0 ||
		input.UnsafeIncorrectMicro == 0 ||
		input.SafeCorrectMicro == 0 ||
		input.SafeIncorrectMicro == 0 {
		return CodeError{Code: "ERR_INVALID_RELIABILITY_COUNTS", Message: input.JudgeID}
	}
	_ = publicKey
	state := JudgeState{
		JudgeID:              input.JudgeID,
		Organization:         input.Organization,
		ModelID:              input.ModelID,
		ModelRevision:        input.ModelRevision,
		PolicySHA256:         strings.ToLower(input.PolicySHA256),
		AdapterVersion:       input.AdapterVersion,
		PublicKeyHex:         strings.ToLower(input.PublicKeyHex),
		PublicKeyFingerprint: fingerprint,
		UnsafeCorrectMicro:   input.UnsafeCorrectMicro,
		UnsafeIncorrectMicro: input.UnsafeIncorrectMicro,
		SafeCorrectMicro:     input.SafeCorrectMicro,
		SafeIncorrectMicro:   input.SafeIncorrectMicro,
		GUnsafePPM:           ratioPPM(input.UnsafeCorrectMicro, input.UnsafeCorrectMicro+input.UnsafeIncorrectMicro),
		GSafePPM:             ratioPPM(input.SafeCorrectMicro, input.SafeCorrectMicro+input.SafeIncorrectMicro),
		Version:              1,
		RegisteredAtUnix:     tx.TimestampUnix(),
	}
	return tx.Put(key, state)
}

func (Service) RegisterValidator(tx Tx, input RegisterValidatorInput) error {
	if err := requireAuditService(tx); err != nil {
		return err
	}
	if strings.TrimSpace(input.ValidatorID) == "" {
		return CodeError{Code: "ERR_INVALID_VALIDATOR", Message: "validator ID is required"}
	}
	if input.ReliabilityPPM < 0 || input.ReliabilityPPM > OnePPM || input.Version < 1 {
		return CodeError{Code: "ERR_INVALID_VALIDATOR", Message: input.ValidatorID}
	}
	_, fingerprint, err := decodeEd25519PublicKey(input.PublicKeyHex)
	if err != nil {
		return err
	}
	key := ValidatorStateKey(input.ValidatorID)
	var existing ValidatorState
	found, err := tx.Get(key, &existing)
	if err != nil {
		return fmt.Errorf("read validator: %w", err)
	}
	if found {
		return CodeError{Code: "ERR_VALIDATOR_EXISTS", Message: input.ValidatorID}
	}
	return tx.Put(key, ValidatorState{
		ValidatorID:          input.ValidatorID,
		PublicKeyHex:         strings.ToLower(input.PublicKeyHex),
		PublicKeyFingerprint: fingerprint,
		ReliabilityPPM:       input.ReliabilityPPM,
		Version:              input.Version,
		RegisteredAtUnix:     tx.TimestampUnix(),
	})
}

func (Service) FreezeDecisionSnapshot(tx Tx, input FreezeDecisionInput) (DecisionSnapshot, error) {
	if err := requireAuditService(tx); err != nil {
		return DecisionSnapshot{}, err
	}
	if strings.TrimSpace(input.DecisionID) == "" ||
		strings.TrimSpace(input.SampleID) == "" ||
		strings.TrimSpace(input.EvidenceCID) == "" {
		return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_DECISION", Message: input.DecisionID}
	}
	if !isSHA256(input.EvidenceSHA256) {
		return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_EVIDENCE_HASH", Message: input.DecisionID}
	}
	if !isBinaryLabel(input.ProvisionalLabel) ||
		input.PosteriorUnsafePPM < 0 || input.PosteriorUnsafePPM > OnePPM {
		return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_DECISION_LABEL", Message: input.ProvisionalLabel}
	}
	if len(input.JudgeOutputs) < 3 ||
		input.CommitteeQuorum < 1 ||
		input.CertificateQuorum < 1 ||
		input.DeadlineUnix <= tx.TimestampUnix() {
		return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_DECISION_POLICY", Message: input.DecisionID}
	}
	leaders, err := validateLeaderCommittee(
		tx, input.LeaderValidatorIDs, input.CertificateQuorum,
	)
	if err != nil {
		return DecisionSnapshot{}, err
	}
	input.LeaderValidatorIDs = leaders
	key := DecisionSnapshotKey(input.DecisionID)
	var existing DecisionSnapshot
	found, err := tx.Get(key, &existing)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read decision: %w", err)
	}
	if found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_EXISTS", Message: input.DecisionID}
	}

	outputs := append([]SignedJudgeOutput(nil), input.JudgeOutputs...)
	sort.Slice(outputs, func(i, j int) bool { return outputs[i].JudgeID < outputs[j].JudgeID })
	reliabilities := make([]FrozenJudgeReliability, 0, len(outputs))
	seen := make(map[string]struct{}, len(outputs))
	for index := range outputs {
		output := &outputs[index]
		output.DecisionID = strings.TrimSpace(output.DecisionID)
		output.Label = strings.ToLower(output.Label)
		output.EvidenceSHA256 = strings.ToLower(output.EvidenceSHA256)
		output.PolicySHA256 = strings.ToLower(output.PolicySHA256)
		output.SignatureHex = strings.ToLower(output.SignatureHex)
		if _, duplicate := seen[output.JudgeID]; duplicate {
			return DecisionSnapshot{}, CodeError{Code: "ERR_DUPLICATE_JUDGE", Message: output.JudgeID}
		}
		seen[output.JudgeID] = struct{}{}
		if output.DecisionID != input.DecisionID ||
			output.SampleID != input.SampleID ||
			output.EvidenceSHA256 != strings.ToLower(input.EvidenceSHA256) {
			return DecisionSnapshot{}, CodeError{Code: "ERR_JUDGE_PAYLOAD_BINDING", Message: output.JudgeID}
		}
		if !isBinaryLabel(output.Label) {
			return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_JUDGE_LABEL", Message: output.JudgeID}
		}
		var judge JudgeState
		found, err = tx.Get(JudgeStateKey(output.JudgeID), &judge)
		if err != nil {
			return DecisionSnapshot{}, fmt.Errorf("read judge %s: %w", output.JudgeID, err)
		}
		if !found {
			return DecisionSnapshot{}, CodeError{Code: "ERR_JUDGE_NOT_FOUND", Message: output.JudgeID}
		}
		if output.PolicySHA256 != judge.PolicySHA256 {
			return DecisionSnapshot{}, CodeError{Code: "ERR_POLICY_HASH_MISMATCH", Message: output.JudgeID}
		}
		if output.AdapterVersion != judge.AdapterVersion {
			return DecisionSnapshot{}, CodeError{Code: "ERR_ADAPTER_VERSION_MISMATCH", Message: output.JudgeID}
		}
		if err := verifyHexSignature(judge.PublicKeyHex, CanonicalJudgeOutput(*output), output.SignatureHex); err != nil {
			return DecisionSnapshot{}, CodeError{Code: "ERR_JUDGE_SIGNATURE", Message: output.JudgeID}
		}
		reliabilities = append(reliabilities, FrozenJudgeReliability{
			JudgeID: output.JudgeID, GUnsafePPM: judge.GUnsafePPM,
			GSafePPM: judge.GSafePPM, Version: judge.Version,
		})
	}
	input.JudgeOutputs = outputs
	input.EvidenceSHA256 = strings.ToLower(input.EvidenceSHA256)
	digest := DecisionSnapshotDigest(input)
	snapshot := DecisionSnapshot{
		DecisionID: input.DecisionID, SampleID: input.SampleID,
		EvidenceCID: input.EvidenceCID, EvidenceSHA256: input.EvidenceSHA256,
		JudgeOutputs: outputs, FrozenReliabilities: reliabilities,
		ProvisionalLabel:   strings.ToLower(input.ProvisionalLabel),
		PosteriorUnsafePPM: input.PosteriorUnsafePPM,
		DecisionDigest:     digest, CommitteeQuorum: input.CommitteeQuorum,
		CertificateQuorum:  input.CertificateQuorum,
		LeaderValidatorIDs: append([]string(nil), input.LeaderValidatorIDs...),
		DeadlineUnix:       input.DeadlineUnix, Status: DecisionStatusSnapshotFrozen,
		CreatedAtUnix: tx.TimestampUnix(),
	}
	if err := tx.Put(key, snapshot); err != nil {
		return DecisionSnapshot{}, fmt.Errorf("write decision: %w", err)
	}
	return snapshot, nil
}

func (Service) FreezeDecisionBatch(tx Tx, input FreezeDecisionBatchInput) (DecisionSnapshot, error) {
	if err := requireAuditService(tx); err != nil {
		return DecisionSnapshot{}, err
	}
	if strings.TrimSpace(input.DecisionID) == "" ||
		strings.TrimSpace(input.EvidenceCID) == "" ||
		!isSHA256(input.EvidenceSHA256) {
		return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_DECISION", Message: input.DecisionID}
	}
	if len(input.Records) < 1 || len(input.Records) > 64 ||
		input.CommitteeQuorum < 1 || input.CertificateQuorum < 1 ||
		input.DeadlineUnix <= tx.TimestampUnix() {
		return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_DECISION_POLICY", Message: input.DecisionID}
	}
	leaders, err := validateLeaderCommittee(
		tx, input.LeaderValidatorIDs, input.CertificateQuorum,
	)
	if err != nil {
		return DecisionSnapshot{}, err
	}
	input.LeaderValidatorIDs = leaders
	key := DecisionSnapshotKey(input.DecisionID)
	var existing DecisionSnapshot
	found, err := tx.Get(key, &existing)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read decision batch: %w", err)
	}
	if found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_EXISTS", Message: input.DecisionID}
	}

	input.EvidenceSHA256 = strings.ToLower(input.EvidenceSHA256)
	records := append([]DecisionRecord(nil), input.Records...)
	sort.Slice(records, func(i, j int) bool { return records[i].SampleID < records[j].SampleID })
	seenSamples := make(map[string]struct{}, len(records))
	judgeSet := make(map[string]FrozenJudgeReliability)
	var expectedJudgeIDs []string
	for recordIndex := range records {
		record := &records[recordIndex]
		record.SampleID = strings.TrimSpace(record.SampleID)
		record.EvidenceSHA256 = strings.ToLower(record.EvidenceSHA256)
		record.ProvisionalLabel = strings.ToLower(record.ProvisionalLabel)
		if record.SampleID == "" || !isSHA256(record.EvidenceSHA256) ||
			!isBinaryLabel(record.ProvisionalLabel) ||
			record.PosteriorUnsafePPM < 0 || record.PosteriorUnsafePPM > OnePPM ||
			len(record.JudgeOutputs) < 3 {
			return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_DECISION_RECORD", Message: record.SampleID}
		}
		if _, duplicate := seenSamples[record.SampleID]; duplicate {
			return DecisionSnapshot{}, CodeError{Code: "ERR_DUPLICATE_SAMPLE", Message: record.SampleID}
		}
		seenSamples[record.SampleID] = struct{}{}

		outputs := append([]SignedJudgeOutput(nil), record.JudgeOutputs...)
		sort.Slice(outputs, func(i, j int) bool { return outputs[i].JudgeID < outputs[j].JudgeID })
		seenJudges := make(map[string]struct{}, len(outputs))
		currentJudgeIDs := make([]string, 0, len(outputs))
		for outputIndex := range outputs {
			output := &outputs[outputIndex]
			output.Label = strings.ToLower(output.Label)
			output.EvidenceSHA256 = strings.ToLower(output.EvidenceSHA256)
			output.PolicySHA256 = strings.ToLower(output.PolicySHA256)
			output.SignatureHex = strings.ToLower(output.SignatureHex)
			if _, duplicate := seenJudges[output.JudgeID]; duplicate {
				return DecisionSnapshot{}, CodeError{Code: "ERR_DUPLICATE_JUDGE", Message: output.JudgeID}
			}
			seenJudges[output.JudgeID] = struct{}{}
			currentJudgeIDs = append(currentJudgeIDs, output.JudgeID)
			if output.DecisionID != input.DecisionID ||
				output.SampleID != record.SampleID ||
				output.EvidenceSHA256 != record.EvidenceSHA256 {
				return DecisionSnapshot{}, CodeError{Code: "ERR_JUDGE_PAYLOAD_BINDING", Message: output.JudgeID}
			}
			if !isBinaryLabel(output.Label) {
				return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_JUDGE_LABEL", Message: output.JudgeID}
			}
			var judge JudgeState
			found, err = tx.Get(JudgeStateKey(output.JudgeID), &judge)
			if err != nil {
				return DecisionSnapshot{}, fmt.Errorf("read judge %s: %w", output.JudgeID, err)
			}
			if !found {
				return DecisionSnapshot{}, CodeError{Code: "ERR_JUDGE_NOT_FOUND", Message: output.JudgeID}
			}
			if output.PolicySHA256 != judge.PolicySHA256 {
				return DecisionSnapshot{}, CodeError{Code: "ERR_POLICY_HASH_MISMATCH", Message: output.JudgeID}
			}
			if output.AdapterVersion != judge.AdapterVersion {
				return DecisionSnapshot{}, CodeError{Code: "ERR_ADAPTER_VERSION_MISMATCH", Message: output.JudgeID}
			}
			if err := verifyHexSignature(judge.PublicKeyHex, CanonicalJudgeOutput(*output), output.SignatureHex); err != nil {
				return DecisionSnapshot{}, CodeError{Code: "ERR_JUDGE_SIGNATURE", Message: output.JudgeID}
			}
			judgeSet[output.JudgeID] = FrozenJudgeReliability{
				JudgeID: output.JudgeID, GUnsafePPM: judge.GUnsafePPM,
				GSafePPM: judge.GSafePPM, Version: judge.Version,
			}
		}
		if recordIndex == 0 {
			expectedJudgeIDs = append([]string(nil), currentJudgeIDs...)
		} else if strings.Join(expectedJudgeIDs, "\x00") != strings.Join(currentJudgeIDs, "\x00") {
			return DecisionSnapshot{}, CodeError{Code: "ERR_INCONSISTENT_JUDGE_SET", Message: record.SampleID}
		}
		record.JudgeOutputs = outputs
	}

	reliabilities := make([]FrozenJudgeReliability, 0, len(judgeSet))
	for _, judgeID := range expectedJudgeIDs {
		reliabilities = append(reliabilities, judgeSet[judgeID])
	}
	input.Records = records
	digest := DecisionBatchDigest(input)
	snapshot := DecisionSnapshot{
		DecisionID: input.DecisionID, EvidenceCID: input.EvidenceCID,
		EvidenceSHA256: input.EvidenceSHA256, Records: records,
		FrozenReliabilities: reliabilities, DecisionDigest: digest,
		CommitteeQuorum:    input.CommitteeQuorum,
		CertificateQuorum:  input.CertificateQuorum,
		LeaderValidatorIDs: append([]string(nil), input.LeaderValidatorIDs...),
		DeadlineUnix:       input.DeadlineUnix, Status: DecisionStatusSnapshotFrozen,
		CreatedAtUnix: tx.TimestampUnix(),
	}
	if err := tx.Put(key, snapshot); err != nil {
		return DecisionSnapshot{}, fmt.Errorf("write decision batch: %w", err)
	}
	return snapshot, nil
}

func (Service) SubmitCommitteeVote(tx Tx, input SubmitCommitteeVoteInput) (DecisionSnapshot, error) {
	var snapshot DecisionSnapshot
	found, err := tx.Get(DecisionSnapshotKey(input.DecisionID), &snapshot)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read decision: %w", err)
	}
	if !found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_FOUND", Message: input.DecisionID}
	}
	if snapshot.Status != DecisionStatusSnapshotFrozen {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_VOTABLE", Message: snapshot.Status}
	}
	if tx.TimestampUnix() > snapshot.DeadlineUnix {
		return DecisionSnapshot{}, CodeError{Code: "ERR_VOTE_CLOSED", Message: input.DecisionID}
	}
	input.VoteType = strings.ToUpper(input.VoteType)
	input.DecisionDigest = strings.ToLower(input.DecisionDigest)
	input.SignatureHex = strings.ToLower(input.SignatureHex)
	if input.DecisionDigest != snapshot.DecisionDigest {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DIGEST_MISMATCH", Message: input.DecisionID}
	}
	if input.VoteType != "ACK" && input.VoteType != "OBJECT" {
		return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_VOTE", Message: input.VoteType}
	}
	var validator ValidatorState
	found, err = tx.Get(ValidatorStateKey(input.ValidatorID), &validator)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read validator: %w", err)
	}
	if !found || input.ValidatorVersion != validator.Version {
		return DecisionSnapshot{}, CodeError{Code: "ERR_VALIDATOR_IDENTITY", Message: input.ValidatorID}
	}
	if err := verifyHexSignature(validator.PublicKeyHex, CanonicalCommitteeVote(input), input.SignatureHex); err != nil {
		return DecisionSnapshot{}, CodeError{Code: "ERR_VALIDATOR_SIGNATURE", Message: input.ValidatorID}
	}
	voteKey := CommitteeVoteKey(input.DecisionID, input.ValidatorID)
	var existing CommitteeVote
	found, err = tx.Get(voteKey, &existing)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read committee vote: %w", err)
	}
	if found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DUPLICATE_VOTE", Message: input.ValidatorID}
	}
	vote := CommitteeVote{
		DecisionID: input.DecisionID, DecisionDigest: input.DecisionDigest,
		ValidatorID: input.ValidatorID, VoteType: input.VoteType,
		ValidatorVersion: input.ValidatorVersion, SignatureHex: input.SignatureHex,
		CreatedAtUnix: tx.TimestampUnix(),
	}
	if err := tx.Put(voteKey, vote); err != nil {
		return DecisionSnapshot{}, fmt.Errorf("write committee vote: %w", err)
	}
	if input.VoteType == "OBJECT" {
		snapshot.ObjectCount++
		snapshot.Status = DecisionStatusReview
		snapshot.ConfirmedAtUnix = tx.TimestampUnix()
	} else {
		snapshot.AckCount++
		if snapshot.AckCount >= snapshot.CommitteeQuorum {
			snapshot.Status = DecisionStatusCommitteeConfirmed
			snapshot.ConfirmedAtUnix = tx.TimestampUnix()
		}
	}
	if err := tx.Put(DecisionSnapshotKey(input.DecisionID), snapshot); err != nil {
		return DecisionSnapshot{}, fmt.Errorf("update decision: %w", err)
	}
	return snapshot, nil
}

func (Service) FinalizeDecisionTimeout(tx Tx, decisionID string) (DecisionSnapshot, error) {
	var snapshot DecisionSnapshot
	found, err := tx.Get(DecisionSnapshotKey(decisionID), &snapshot)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read decision: %w", err)
	}
	if !found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_FOUND", Message: decisionID}
	}
	if snapshot.Status != DecisionStatusSnapshotFrozen {
		return snapshot, nil
	}
	if tx.TimestampUnix() <= snapshot.DeadlineUnix {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DEADLINE_NOT_REACHED", Message: decisionID}
	}
	snapshot.Status = DecisionStatusReview
	snapshot.ConfirmedAtUnix = tx.TimestampUnix()
	if err := tx.Put(DecisionSnapshotKey(decisionID), snapshot); err != nil {
		return DecisionSnapshot{}, fmt.Errorf("update decision: %w", err)
	}
	return snapshot, nil
}

func (Service) CertifyDecision(tx Tx, input CertifyDecisionInput) (DecisionSnapshot, error) {
	if err := requireAuditService(tx); err != nil {
		return DecisionSnapshot{}, err
	}
	var snapshot DecisionSnapshot
	found, err := tx.Get(DecisionSnapshotKey(input.DecisionID), &snapshot)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read decision: %w", err)
	}
	if !found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_FOUND", Message: input.DecisionID}
	}
	input.DecisionDigest = strings.ToLower(input.DecisionDigest)
	if snapshot.Status != DecisionStatusCommitteeConfirmed {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_CONFIRMED", Message: snapshot.Status}
	}
	if input.DecisionDigest != snapshot.DecisionDigest {
		return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_DIGEST_MISMATCH", Message: input.DecisionID}
	}
	if int64(len(input.ProtocolMessages)) < snapshot.CertificateQuorum {
		return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_QUORUM", Message: input.DecisionID}
	}
	allowedLeaders := make(map[string]struct{}, len(snapshot.LeaderValidatorIDs))
	for _, validatorID := range snapshot.LeaderValidatorIDs {
		allowedLeaders[validatorID] = struct{}{}
	}
	seen := make(map[string]struct{}, len(input.ProtocolMessages))
	messages := append([]RGGProtocolMessage(nil), input.ProtocolMessages...)
	signers := make([]CertificateSigner, 0, len(messages))
	for index := range messages {
		protocolMessage := &messages[index]
		protocolMessage.Digest = strings.ToLower(protocolMessage.Digest)
		protocolMessage.Signature = strings.TrimSpace(protocolMessage.Signature)
		if protocolMessage.Type != "GLOBAL_COMMIT" ||
			protocolMessage.View != input.View ||
			protocolMessage.Sequence != input.Sequence ||
			protocolMessage.Digest != input.DecisionDigest ||
			protocolMessage.Group != -1 {
			return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_TUPLE", Message: input.DecisionID}
		}
		validatorID := fmt.Sprintf("validator-%02d", protocolMessage.Sender)
		if protocolMessage.Sender < 0 {
			return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_SIGNER_NOT_LEADER", Message: validatorID}
		}
		if _, allowed := allowedLeaders[validatorID]; !allowed {
			return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_SIGNER_NOT_LEADER", Message: validatorID}
		}
		if _, duplicate := seen[validatorID]; duplicate {
			return DecisionSnapshot{}, CodeError{Code: "ERR_DUPLICATE_CERTIFICATE_SIGNER", Message: validatorID}
		}
		seen[validatorID] = struct{}{}
		var validator ValidatorState
		found, err = tx.Get(ValidatorStateKey(validatorID), &validator)
		if err != nil {
			return DecisionSnapshot{}, fmt.Errorf("read validator %s: %w", validatorID, err)
		}
		if !found {
			return DecisionSnapshot{}, CodeError{Code: "ERR_VALIDATOR_IDENTITY", Message: validatorID}
		}
		signature, decodeErr := base64.StdEncoding.DecodeString(protocolMessage.Signature)
		if decodeErr != nil || len(signature) != ed25519.SignatureSize {
			return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_SIGNATURE", Message: validatorID}
		}
		canonical, canonicalErr := CanonicalRGGProtocolMessage(*protocolMessage)
		if canonicalErr != nil {
			return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_PAYLOAD", Message: validatorID}
		}
		publicKey, _, publicKeyErr := decodeEd25519PublicKey(validator.PublicKeyHex)
		if publicKeyErr != nil || !ed25519.Verify(publicKey, canonical, signature) {
			return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_SIGNATURE", Message: validatorID}
		}
		signers = append(signers, CertificateSigner{
			ValidatorID:      validatorID,
			ValidatorVersion: validator.Version,
			SignatureHex:     hex.EncodeToString(signature),
		})
	}
	if int64(len(seen)) < snapshot.CertificateQuorum {
		return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_QUORUM", Message: input.DecisionID}
	}
	sort.Slice(messages, func(i, j int) bool { return messages[i].Sender < messages[j].Sender })
	sort.Slice(signers, func(i, j int) bool { return signers[i].ValidatorID < signers[j].ValidatorID })
	protocolHash, err := ProtocolCertificateDigest(messages)
	if err != nil {
		return DecisionSnapshot{}, CodeError{Code: "ERR_CERTIFICATE_PAYLOAD", Message: input.DecisionID}
	}
	certificate := RGGCertificate{
		DecisionID: input.DecisionID, DecisionDigest: input.DecisionDigest,
		View: input.View, Sequence: input.Sequence, Signers: signers,
		ProtocolMessages: messages, ProtocolCertificateSHA: protocolHash,
		Verified: true, CreatedAtUnix: tx.TimestampUnix(),
	}
	certificate.CertificateSHA = CertificateDigest(certificate)
	snapshot.Certificate = &certificate
	snapshot.Status = DecisionStatusCertified
	snapshot.CertifiedAtUnix = tx.TimestampUnix()
	if err := tx.Put(DecisionSnapshotKey(input.DecisionID), snapshot); err != nil {
		return DecisionSnapshot{}, fmt.Errorf("update decision: %w", err)
	}
	return snapshot, nil
}

func (Service) SettleDecision(tx Tx, input SettleDecisionInput) (DecisionSnapshot, error) {
	if err := requireAuditService(tx); err != nil {
		return DecisionSnapshot{}, err
	}
	var snapshot DecisionSnapshot
	found, err := tx.Get(DecisionSnapshotKey(input.DecisionID), &snapshot)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read decision: %w", err)
	}
	if !found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_FOUND", Message: input.DecisionID}
	}
	if snapshot.Settled || snapshot.Status == DecisionStatusSettled {
		return DecisionSnapshot{}, CodeError{Code: "ERR_ALREADY_SETTLED", Message: input.DecisionID}
	}
	if snapshot.Status != DecisionStatusCertified || snapshot.Certificate == nil ||
		!snapshot.Certificate.Verified ||
		snapshot.Certificate.DecisionDigest != snapshot.DecisionDigest {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_CERTIFIED", Message: snapshot.Status}
	}

	type settlementRecord struct {
		label   string
		outputs []SignedJudgeOutput
	}
	settlementRecords := make([]settlementRecord, 0, max(1, len(snapshot.Records)))
	if len(snapshot.Records) == 0 {
		input.IndependentLabel = strings.ToLower(input.IndependentLabel)
		if !isBinaryLabel(input.IndependentLabel) || len(input.IndependentLabels) != 0 {
			return DecisionSnapshot{}, CodeError{Code: "ERR_INVALID_SETTLEMENT_LABEL", Message: input.IndependentLabel}
		}
		settlementRecords = append(settlementRecords, settlementRecord{
			label: input.IndependentLabel, outputs: snapshot.JudgeOutputs,
		})
		snapshot.SettlementLabel = input.IndependentLabel
	} else {
		if input.IndependentLabel != "" || len(input.IndependentLabels) != len(snapshot.Records) {
			return DecisionSnapshot{}, CodeError{Code: "ERR_SETTLEMENT_MEMBERSHIP", Message: input.DecisionID}
		}
		labels := make(map[string]string, len(input.IndependentLabels))
		for _, item := range input.IndependentLabels {
			item.SampleID = strings.TrimSpace(item.SampleID)
			item.Label = strings.ToLower(item.Label)
			if _, duplicate := labels[item.SampleID]; duplicate || !isBinaryLabel(item.Label) {
				return DecisionSnapshot{}, CodeError{Code: "ERR_SETTLEMENT_MEMBERSHIP", Message: item.SampleID}
			}
			labels[item.SampleID] = item.Label
		}
		for _, record := range snapshot.Records {
			label, ok := labels[record.SampleID]
			if !ok {
				return DecisionSnapshot{}, CodeError{Code: "ERR_SETTLEMENT_MEMBERSHIP", Message: record.SampleID}
			}
			settlementRecords = append(settlementRecords, settlementRecord{
				label: label, outputs: record.JudgeOutputs,
			})
		}
		snapshot.SettlementLabel = fmt.Sprintf("batch:%d", len(snapshot.Records))
	}

	for _, record := range settlementRecords {
		for _, output := range record.outputs {
			var judge JudgeState
			found, err = tx.Get(JudgeStateKey(output.JudgeID), &judge)
			if err != nil {
				return DecisionSnapshot{}, fmt.Errorf("read judge %s: %w", output.JudgeID, err)
			}
			if !found {
				return DecisionSnapshot{}, CodeError{Code: "ERR_JUDGE_NOT_FOUND", Message: output.JudgeID}
			}
			if record.label == "unsafe" {
				if output.Label == "unsafe" {
					judge.UnsafeCorrectMicro += OnePPM
				} else {
					judge.UnsafeIncorrectMicro += OnePPM
				}
			} else if output.Label == "safe" {
				judge.SafeCorrectMicro += OnePPM
			} else {
				judge.SafeIncorrectMicro += OnePPM
			}
			judge.GUnsafePPM = ratioPPM(judge.UnsafeCorrectMicro, judge.UnsafeCorrectMicro+judge.UnsafeIncorrectMicro)
			judge.GSafePPM = ratioPPM(judge.SafeCorrectMicro, judge.SafeCorrectMicro+judge.SafeIncorrectMicro)
			judge.Version++
			judge.LastSettlementAtUnix = tx.TimestampUnix()
			if err := tx.Put(JudgeStateKey(judge.JudgeID), judge); err != nil {
				return DecisionSnapshot{}, fmt.Errorf("update judge %s: %w", judge.JudgeID, err)
			}
		}
	}
	snapshot.Status = DecisionStatusSettled
	snapshot.Settled = true
	snapshot.SettledAtUnix = tx.TimestampUnix()
	if err := tx.Put(DecisionSnapshotKey(input.DecisionID), snapshot); err != nil {
		return DecisionSnapshot{}, fmt.Errorf("update decision: %w", err)
	}
	return snapshot, nil
}

func (Service) QueryJudge(tx Tx, judgeID string) (JudgeState, error) {
	var judge JudgeState
	found, err := tx.Get(JudgeStateKey(judgeID), &judge)
	if err != nil {
		return JudgeState{}, fmt.Errorf("read judge: %w", err)
	}
	if !found {
		return JudgeState{}, CodeError{Code: "ERR_JUDGE_NOT_FOUND", Message: judgeID}
	}
	return judge, nil
}

func (Service) QueryValidator(tx Tx, validatorID string) (ValidatorState, error) {
	var validator ValidatorState
	found, err := tx.Get(ValidatorStateKey(validatorID), &validator)
	if err != nil {
		return ValidatorState{}, fmt.Errorf("read validator: %w", err)
	}
	if !found {
		return ValidatorState{}, CodeError{Code: "ERR_VALIDATOR_NOT_FOUND", Message: validatorID}
	}
	return validator, nil
}

func (Service) QueryDecisionSnapshot(tx Tx, decisionID string) (DecisionSnapshot, error) {
	var snapshot DecisionSnapshot
	found, err := tx.Get(DecisionSnapshotKey(decisionID), &snapshot)
	if err != nil {
		return DecisionSnapshot{}, fmt.Errorf("read decision: %w", err)
	}
	if !found {
		return DecisionSnapshot{}, CodeError{Code: "ERR_DECISION_NOT_FOUND", Message: decisionID}
	}
	return snapshot, nil
}

func requireAuditService(tx Tx) error {
	if tx.Caller().Attributes["role"] != "audit_service" {
		return CodeError{Code: "ERR_UNAUTHORIZED", Message: "audit_service role is required"}
	}
	return nil
}

func validateLeaderCommittee(
	tx Tx,
	validatorIDs []string,
	certificateQuorum int64,
) ([]string, error) {
	if len(validatorIDs) == 0 {
		return nil, CodeError{Code: "ERR_INVALID_LEADER_COMMITTEE", Message: "leader committee is empty"}
	}
	expectedQuorum := int64(2*((len(validatorIDs)-1)/3) + 1)
	if certificateQuorum != expectedQuorum {
		return nil, CodeError{
			Code: "ERR_INVALID_CERTIFICATE_QUORUM",
			Message: fmt.Sprintf(
				"committee size %d requires quorum %d",
				len(validatorIDs),
				expectedQuorum,
			),
		}
	}
	normalized := make([]string, 0, len(validatorIDs))
	seen := make(map[string]struct{}, len(validatorIDs))
	for _, value := range validatorIDs {
		validatorID := strings.TrimSpace(value)
		if validatorID == "" {
			return nil, CodeError{Code: "ERR_INVALID_LEADER_COMMITTEE", Message: "empty validator ID"}
		}
		if _, duplicate := seen[validatorID]; duplicate {
			return nil, CodeError{Code: "ERR_INVALID_LEADER_COMMITTEE", Message: validatorID}
		}
		var validator ValidatorState
		found, err := tx.Get(ValidatorStateKey(validatorID), &validator)
		if err != nil {
			return nil, fmt.Errorf("read validator %s: %w", validatorID, err)
		}
		if !found {
			return nil, CodeError{Code: "ERR_VALIDATOR_NOT_FOUND", Message: validatorID}
		}
		seen[validatorID] = struct{}{}
		normalized = append(normalized, validatorID)
	}
	return normalized, nil
}

func decodeEd25519PublicKey(value string) (ed25519.PublicKey, string, error) {
	decoded, err := hex.DecodeString(strings.TrimSpace(value))
	if err != nil || len(decoded) != ed25519.PublicKeySize {
		return nil, "", CodeError{Code: "ERR_INVALID_PUBLIC_KEY", Message: "expected an Ed25519 public key"}
	}
	sum := sha256.Sum256(decoded)
	return ed25519.PublicKey(decoded), hex.EncodeToString(sum[:]), nil
}

func verifyHexSignature(publicKeyHex string, message []byte, signatureHex string) error {
	publicKey, _, err := decodeEd25519PublicKey(publicKeyHex)
	if err != nil {
		return err
	}
	signature, err := hex.DecodeString(strings.TrimSpace(signatureHex))
	if err != nil || len(signature) != ed25519.SignatureSize ||
		!ed25519.Verify(publicKey, message, signature) {
		return CodeError{Code: "ERR_INVALID_SIGNATURE", Message: "Ed25519 verification failed"}
	}
	return nil
}

func ratioPPM(numerator, denominator int64) int64 {
	if denominator <= 0 {
		return 0
	}
	return (numerator*OnePPM + denominator/2) / denominator
}

func isBinaryLabel(value string) bool {
	return value == "safe" || value == "unsafe"
}
