package internal

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

type Record struct {
	Sequence  int64  `json:"seq"`
	Timestamp int64  `json:"ts"`
	Op        string `json:"op"`
	TaskID    string `json:"taskId"`
	CID       string `json:"cid,omitempty"`
	SHA256    string `json:"sha256,omitempty"`
	Payload   string `json:"payload,omitempty"`
	PrevHash  string `json:"prevHash"`
	SignerID  string `json:"signerId"`
	Signature string `json:"sig"`
}

type SignedLog struct {
	mu       sync.Mutex
	file     *os.File
	encoder  *json.Encoder
	sequence int64
	prevHash string
	signerID string
	privKey  ed25519.PrivateKey
	pubKey   ed25519.PublicKey
	records  []Record
	latest   map[string]Record
}

type NewLogInput struct {
	Path     string
	SignerID string
}

func NewSignedLog(input NewLogInput) (*SignedLog, error) {
	f, err := os.OpenFile(input.Path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return nil, fmt.Errorf("open log: %w", err)
	}
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		f.Close()
		return nil, fmt.Errorf("generate key: %w", err)
	}
	return &SignedLog{
		file:     f,
		encoder:  json.NewEncoder(f),
		signerID: input.SignerID,
		privKey:  priv,
		pubKey:   pub,
		prevHash: "GENESIS",
		latest:   make(map[string]Record),
	}, nil
}

func (l *SignedLog) Append(op, taskID, cid, sha256hex, payload string) (Record, error) {
	l.mu.Lock()
	defer l.mu.Unlock()

	rec := Record{
		Sequence:  l.sequence + 1,
		Timestamp: time.Now().UnixMilli(),
		Op:        op,
		TaskID:    taskID,
		CID:       cid,
		SHA256:    sha256hex,
		Payload:   payload,
		PrevHash:  l.prevHash,
		SignerID:  l.signerID,
	}

	sig, err := l.signRecord(rec)
	if err != nil {
		return Record{}, err
	}
	rec.Signature = hex.EncodeToString(sig)

	if err := l.encoder.Encode(rec); err != nil {
		return Record{}, fmt.Errorf("write record: %w", err)
	}
	if err := l.file.Sync(); err != nil {
		return Record{}, fmt.Errorf("sync record: %w", err)
	}
	l.sequence = rec.Sequence
	l.prevHash = l.hashRecord(rec)
	l.records = append(l.records, rec)
	l.latest[taskID] = rec
	return rec, nil
}

func (l *SignedLog) Latest(taskID string) (Record, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	record, found := l.latest[taskID]
	return record, found
}

func (l *SignedLog) signRecord(rec Record) ([]byte, error) {
	data, _ := json.Marshal(RecordForSigning{
		Sequence:  rec.Sequence,
		Timestamp: rec.Timestamp,
		Op:        rec.Op,
		TaskID:    rec.TaskID,
		CID:       rec.CID,
		SHA256:    rec.SHA256,
		Payload:   rec.Payload,
		PrevHash:  rec.PrevHash,
		SignerID:  rec.SignerID,
	})
	return ed25519.Sign(l.privKey, data), nil
}

type RecordForSigning struct {
	Sequence  int64  `json:"seq"`
	Timestamp int64  `json:"ts"`
	Op        string `json:"op"`
	TaskID    string `json:"taskId"`
	CID       string `json:"cid,omitempty"`
	SHA256    string `json:"sha256,omitempty"`
	Payload   string `json:"payload,omitempty"`
	PrevHash  string `json:"prevHash"`
	SignerID  string `json:"signerId"`
}

func (l *SignedLog) hashRecord(rec Record) string {
	data, _ := json.Marshal(rec)
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func (l *SignedLog) PublicKey() ed25519.PublicKey {
	return append(ed25519.PublicKey(nil), l.pubKey...)
}

func (l *SignedLog) Close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.file.Close()
}

type Checkpoint struct {
	RecordCount int64  `json:"recordCount"`
	FinalHash   string `json:"finalHash"`
	SignerID    string `json:"signerId"`
	Signature   string `json:"signature"`
}

type checkpointForSigning struct {
	RecordCount int64  `json:"recordCount"`
	FinalHash   string `json:"finalHash"`
	SignerID    string `json:"signerId"`
}

func (l *SignedLog) Checkpoint() (Checkpoint, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	unsigned := checkpointForSigning{RecordCount: l.sequence, FinalHash: l.prevHash, SignerID: l.signerID}
	data, err := json.Marshal(unsigned)
	if err != nil {
		return Checkpoint{}, fmt.Errorf("encode checkpoint: %w", err)
	}
	signature := ed25519.Sign(l.privKey, data)
	return Checkpoint{
		RecordCount: unsigned.RecordCount,
		FinalHash:   unsigned.FinalHash,
		SignerID:    unsigned.SignerID,
		Signature:   hex.EncodeToString(signature),
	}, nil
}

type TamperCheck struct {
	Valid        bool     `json:"valid"`
	RecordCount  int      `json:"recordCount"`
	HashBrokenAt int64    `json:"hashBrokenAt,omitempty"`
	SigBrokenAt  int64    `json:"sigBrokenAt,omitempty"`
	Errors       []string `json:"errors,omitempty"`
}

func VerifyLog(path string, pubKey ed25519.PublicKey) (TamperCheck, []Record, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return TamperCheck{Valid: false}, nil, err
	}

	check := TamperCheck{Valid: true}
	var records []Record
	var prevHash string

	lines := splitJSONLLines(data)
	for i, line := range lines {
		if len(line) == 0 {
			continue
		}
		var rec Record
		if err := json.Unmarshal(line, &rec); err != nil {
			check.Errors = append(check.Errors, fmt.Sprintf("line %d: parse: %v", i, err))
			check.Valid = false
			continue
		}
		records = append(records, rec)
		check.RecordCount++

		// Verify sequence and hash chain, including the genesis link.
		expectedSequence := int64(check.RecordCount)
		expectedPrevHash := prevHash
		if i == 0 {
			expectedPrevHash = "GENESIS"
		}
		if rec.Sequence != expectedSequence || rec.PrevHash != expectedPrevHash {
			check.HashBrokenAt = rec.Sequence
			check.Valid = false
			check.Errors = append(check.Errors, fmt.Sprintf("seq %d: sequence or hash chain broken", rec.Sequence))
		}

		// Verify signature
		sigBytes, err := hex.DecodeString(rec.Signature)
		if err != nil {
			check.SigBrokenAt = rec.Sequence
			check.Valid = false
			check.Errors = append(check.Errors, fmt.Sprintf("seq %d: bad signature encoding", rec.Sequence))
		} else {
			signingData, _ := json.Marshal(RecordForSigning{
				Sequence:  rec.Sequence,
				Timestamp: rec.Timestamp,
				Op:        rec.Op,
				TaskID:    rec.TaskID,
				CID:       rec.CID,
				SHA256:    rec.SHA256,
				Payload:   rec.Payload,
				PrevHash:  rec.PrevHash,
				SignerID:  rec.SignerID,
			})
			if !ed25519.Verify(pubKey, signingData, sigBytes) {
				check.SigBrokenAt = rec.Sequence
				check.Valid = false
				check.Errors = append(check.Errors, fmt.Sprintf("seq %d: signature invalid", rec.Sequence))
			}
		}
		prevHash = sha256Of(line)
	}

	return check, records, nil
}

func VerifyLogWithCheckpoint(path string, pubKey ed25519.PublicKey, checkpoint Checkpoint) (TamperCheck, []Record, error) {
	check, records, err := VerifyLog(path, pubKey)
	if err != nil {
		return check, records, err
	}
	unsigned := checkpointForSigning{
		RecordCount: checkpoint.RecordCount,
		FinalHash:   checkpoint.FinalHash,
		SignerID:    checkpoint.SignerID,
	}
	data, err := json.Marshal(unsigned)
	if err != nil {
		return check, records, err
	}
	signature, err := hex.DecodeString(checkpoint.Signature)
	if err != nil || !ed25519.Verify(pubKey, data, signature) {
		check.Valid = false
		check.Errors = append(check.Errors, "checkpoint signature invalid")
	}
	finalHash := "GENESIS"
	if len(records) > 0 {
		encoded, marshalErr := json.Marshal(records[len(records)-1])
		if marshalErr != nil {
			return check, records, marshalErr
		}
		finalHash = sha256Of(encoded)
	}
	if int64(check.RecordCount) != checkpoint.RecordCount || finalHash != checkpoint.FinalHash {
		check.Valid = false
		check.Errors = append(check.Errors, "checkpoint record count or final hash mismatch")
	}
	return check, records, nil
}

func sha256Of(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func splitJSONLLines(data []byte) [][]byte {
	var lines [][]byte
	start := 0
	for i, b := range data {
		if b == '\n' {
			trimmed := trimSpace(data[start:i])
			if len(trimmed) > 0 {
				lines = append(lines, trimmed)
			}
			start = i + 1
		}
	}
	if start < len(data) {
		trimmed := trimSpace(data[start:])
		if len(trimmed) > 0 {
			lines = append(lines, trimmed)
		}
	}
	return lines
}

func trimSpace(b []byte) []byte {
	for len(b) > 0 && (b[0] == ' ' || b[0] == '\t' || b[0] == '\r') {
		b = b[1:]
	}
	for len(b) > 0 && (b[len(b)-1] == ' ' || b[len(b)-1] == '\t' || b[len(b)-1] == '\r') {
		b = b[:len(b)-1]
	}
	return b
}
