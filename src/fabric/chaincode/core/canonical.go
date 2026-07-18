package core

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
)

func ConfirmationDigest(taskID string, evalItems []EvalItem, evidenceRefs []EvidenceRef, deadline int64) (string, error) {
	evals := append([]EvalItem(nil), evalItems...)
	refs := append([]EvidenceRef(nil), evidenceRefs...)
	sort.Slice(evals, func(i, j int) bool { return evals[i].EvalID < evals[j].EvalID })
	sort.Slice(refs, func(i, j int) bool { return refs[i].EvalID < refs[j].EvalID })

	evalJSON, err := json.Marshal(evals)
	if err != nil {
		return "", fmt.Errorf("marshal evaluation snapshot: %w", err)
	}
	refJSON, err := json.Marshal(refs)
	if err != nil {
		return "", fmt.Errorf("marshal evidence snapshot: %w", err)
	}
	evalHash := sha256.Sum256(evalJSON)
	refHash := sha256.Sum256(refJSON)

	var canonical bytes.Buffer
	writeLengthPrefixed(&canonical, []byte(taskID))
	writeLengthPrefixed(&canonical, evalHash[:])
	writeLengthPrefixed(&canonical, refHash[:])
	var encodedDeadline [8]byte
	binary.BigEndian.PutUint64(encodedDeadline[:], uint64(deadline))
	writeLengthPrefixed(&canonical, encodedDeadline[:])

	digest := sha256.Sum256(canonical.Bytes())
	return hex.EncodeToString(digest[:]), nil
}

func writeLengthPrefixed(buffer *bytes.Buffer, value []byte) {
	var length [8]byte
	binary.BigEndian.PutUint64(length[:], uint64(len(value)))
	buffer.Write(length[:])
	buffer.Write(value)
}
