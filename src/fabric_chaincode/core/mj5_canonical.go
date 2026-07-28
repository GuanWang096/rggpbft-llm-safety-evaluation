package core

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strconv"
)

func CanonicalJudgeOutput(output SignedJudgeOutput) []byte {
	return canonicalFields(
		"MJ5-JUDGE-OUTPUT-v1",
		output.JudgeID,
		output.DecisionID,
		output.SampleID,
		output.Label,
		output.EvidenceSHA256,
		output.PolicySHA256,
		output.AdapterVersion,
	)
}

func CanonicalCommitteeVote(input SubmitCommitteeVoteInput) []byte {
	return canonicalFields(
		"MJ5-COMMITTEE-VOTE-v1",
		input.DecisionID,
		input.DecisionDigest,
		input.ValidatorID,
		input.VoteType,
		strconv.FormatInt(input.ValidatorVersion, 10),
	)
}

func CanonicalCertificateMessage(
	decisionID, decisionDigest string,
	view, sequence int64,
) []byte {
	return canonicalFields(
		"MJ5-RGG-CERTIFICATE-v1",
		decisionID,
		decisionDigest,
		strconv.FormatInt(view, 10),
		strconv.FormatInt(sequence, 10),
	)
}

func DecisionSnapshotDigest(input FreezeDecisionInput) string {
	outputs := append([]SignedJudgeOutput(nil), input.JudgeOutputs...)
	sort.Slice(outputs, func(i, j int) bool {
		return outputs[i].JudgeID < outputs[j].JudgeID
	})
	fields := []string{
		"MJ5-DECISION-SNAPSHOT-v1",
		input.DecisionID,
		input.SampleID,
		input.EvidenceCID,
		input.EvidenceSHA256,
		input.ProvisionalLabel,
		strconv.FormatInt(input.PosteriorUnsafePPM, 10),
		strconv.FormatInt(input.CommitteeQuorum, 10),
		strconv.FormatInt(input.CertificateQuorum, 10),
		strconv.FormatInt(input.DeadlineUnix, 10),
	}
	fields = append(fields, input.LeaderValidatorIDs...)
	for _, output := range outputs {
		fields = append(fields,
			output.JudgeID,
			output.Label,
			output.PolicySHA256,
			output.AdapterVersion,
			output.SignatureHex,
		)
	}
	sum := sha256.Sum256(canonicalFields(fields...))
	return hex.EncodeToString(sum[:])
}

func DecisionBatchDigest(input FreezeDecisionBatchInput) string {
	records := append([]DecisionRecord(nil), input.Records...)
	sort.Slice(records, func(i, j int) bool {
		return records[i].SampleID < records[j].SampleID
	})
	fields := []string{
		"MJ5-DECISION-BATCH-v1",
		input.DecisionID,
		input.EvidenceCID,
		input.EvidenceSHA256,
		strconv.FormatInt(input.CommitteeQuorum, 10),
		strconv.FormatInt(input.CertificateQuorum, 10),
		strconv.FormatInt(input.DeadlineUnix, 10),
	}
	fields = append(fields, input.LeaderValidatorIDs...)
	for _, record := range records {
		fields = append(fields,
			record.SampleID,
			record.EvidenceSHA256,
			record.ProvisionalLabel,
			strconv.FormatInt(record.PosteriorUnsafePPM, 10),
		)
		outputs := append([]SignedJudgeOutput(nil), record.JudgeOutputs...)
		sort.Slice(outputs, func(i, j int) bool {
			return outputs[i].JudgeID < outputs[j].JudgeID
		})
		for _, output := range outputs {
			fields = append(fields,
				output.JudgeID,
				output.Label,
				output.PolicySHA256,
				output.AdapterVersion,
				output.SignatureHex,
			)
		}
	}
	sum := sha256.Sum256(canonicalFields(fields...))
	return hex.EncodeToString(sum[:])
}

func CertificateDigest(certificate RGGCertificate) string {
	signers := append([]CertificateSigner(nil), certificate.Signers...)
	sort.Slice(signers, func(i, j int) bool {
		return signers[i].ValidatorID < signers[j].ValidatorID
	})
	fields := []string{
		"MJ5-RGG-CERTIFICATE-DIGEST-v1",
		certificate.DecisionID,
		certificate.DecisionDigest,
		strconv.FormatInt(certificate.View, 10),
		strconv.FormatInt(certificate.Sequence, 10),
		certificate.ProtocolCertificateSHA,
	}
	for _, signer := range signers {
		fields = append(fields,
			signer.ValidatorID,
			strconv.FormatInt(signer.ValidatorVersion, 10),
			signer.SignatureHex,
		)
	}
	sum := sha256.Sum256(canonicalFields(fields...))
	return hex.EncodeToString(sum[:])
}

func CanonicalRGGProtocolMessage(message RGGProtocolMessage) ([]byte, error) {
	payload, err := decodeCanonicalJSON(message.Payload)
	if err != nil {
		return nil, err
	}
	unsigned := map[string]any{
		"digest":   message.Digest,
		"group":    message.Group,
		"payload":  payload,
		"sender":   message.Sender,
		"sequence": message.Sequence,
		"type":     message.Type,
		"view":     message.View,
	}
	return json.Marshal(unsigned)
}

func ProtocolCertificateDigest(messages []RGGProtocolMessage) (string, error) {
	ordered := append([]RGGProtocolMessage(nil), messages...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Sender < ordered[j].Sender })
	normalized := make([]map[string]any, 0, len(ordered))
	for _, message := range ordered {
		payload, err := decodeCanonicalJSON(message.Payload)
		if err != nil {
			return "", err
		}
		normalized = append(normalized, map[string]any{
			"digest":    message.Digest,
			"group":     message.Group,
			"payload":   payload,
			"sender":    message.Sender,
			"sequence":  message.Sequence,
			"signature": message.Signature,
			"type":      message.Type,
			"view":      message.View,
		})
	}
	encoded, err := json.Marshal(normalized)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:]), nil
}

func decodeCanonicalJSON(raw json.RawMessage) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, fmt.Errorf("invalid protocol payload JSON: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("invalid protocol payload JSON: trailing value")
		}
		return nil, fmt.Errorf("invalid protocol payload JSON: %w", err)
	}
	return value, nil
}

func canonicalFields(fields ...string) []byte {
	var buffer bytes.Buffer
	for _, field := range fields {
		value := []byte(field)
		var length [8]byte
		binary.BigEndian.PutUint64(length[:], uint64(len(value)))
		buffer.Write(length[:])
		buffer.Write(value)
	}
	return buffer.Bytes()
}
