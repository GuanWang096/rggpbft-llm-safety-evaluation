package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math/rand"
	"os"
	"path/filepath"
	"testing"
)

func TestGeneratePayloadExactAndDeterministic(t *testing.T) {
	one, err := generatePayload(rand.New(rand.NewSource(7)), "task-1", 1, 1024)
	if err != nil {
		t.Fatal(err)
	}
	two, err := generatePayload(rand.New(rand.NewSource(7)), "task-1", 1, 1024)
	if err != nil {
		t.Fatal(err)
	}
	if len(one) != 1024 || !bytes.Equal(one, two) {
		t.Fatal("payload size or determinism mismatch")
	}
}

func TestLoadManifestPayloadsPreservesOrderAndRecordCounts(t *testing.T) {
	dir := t.TempDir()
	payloads := [][]byte{[]byte("first"), []byte("second")}
	batches := make([]map[string]any, 0, len(payloads))
	for index, payload := range payloads {
		name := "batch-00" + string(rune('0'+index)) + ".json"
		if err := os.WriteFile(filepath.Join(dir, name), payload, 0644); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(payload)
		batches = append(batches, map[string]any{
			"batch_index":  index,
			"filename":     name,
			"record_count": index + 1,
			"bytes":        len(payload),
			"sha256":       hex.EncodeToString(digest[:]),
		})
	}
	manifest, _ := json.Marshal(map[string]any{"record_count": 3, "batches": batches})
	manifestPath := filepath.Join(dir, "manifest.json")
	if err := os.WriteFile(manifestPath, manifest, 0644); err != nil {
		t.Fatal(err)
	}

	loaded, err := loadManifestPayloads(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded) != 2 || loaded[0].RecordCount != 1 || loaded[1].RecordCount != 2 {
		t.Fatalf("unexpected payload metadata: %+v", loaded)
	}
	if !bytes.Equal(loaded[0].Data, payloads[0]) || !bytes.Equal(loaded[1].Data, payloads[1]) {
		t.Fatal("payload order or bytes changed")
	}
}

func TestLoadManifestPayloadsRejectsHashMismatch(t *testing.T) {
	dir := t.TempDir()
	name := "batch-000.json"
	if err := os.WriteFile(filepath.Join(dir, name), []byte("payload"), 0644); err != nil {
		t.Fatal(err)
	}
	manifest, _ := json.Marshal(map[string]any{
		"record_count": 1,
		"batches": []map[string]any{{
			"batch_index": 0, "filename": name, "record_count": 1,
			"bytes": 7, "sha256": string(make([]byte, 64)),
		}},
	})
	manifestPath := filepath.Join(dir, "manifest.json")
	if err := os.WriteFile(manifestPath, manifest, 0644); err != nil {
		t.Fatal(err)
	}

	if _, err := loadManifestPayloads(manifestPath); err == nil {
		t.Fatal("hash mismatch was accepted")
	}
}
