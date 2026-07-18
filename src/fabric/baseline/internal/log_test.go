package internal

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func tmpLogPath(t *testing.T) string {
	return filepath.Join(t.TempDir(), "log.jsonl")
}

func TestAppendAndVerify(t *testing.T) {
	path := tmpLogPath(t)
	log, err := NewSignedLog(NewLogInput{Path: path, SignerID: "test-signer"})
	if err != nil {
		t.Fatal(err)
	}

	rec, err := log.Append("PostTask", "t1", "QmTest", "aabbccdd"+"00"+repeat("0", 54), `{"score":850000}`)
	if err != nil {
		t.Fatal(err)
	}
	if rec.Sequence != 1 || rec.TaskID != "t1" || rec.PrevHash != "GENESIS" {
		t.Fatalf("unexpected record: %+v", rec)
	}
	if rec.Signature == "" {
		t.Fatal("signature is empty")
	}

	rec2, err := log.Append("PostTask", "t2", "QmTest2", "bbccddee"+"00"+repeat("0", 54), `{"score":720000}`)
	if err != nil {
		t.Fatal(err)
	}
	if rec2.Sequence != 2 || rec2.PrevHash == "GENESIS" || rec2.PrevHash == "" {
		t.Fatal("prevHash should not be GENESIS for seq>1")
	}
	latest, found := log.Latest("t2")
	if !found || latest.Sequence != rec2.Sequence {
		t.Fatalf("Latest(t2) = %+v, %v", latest, found)
	}

	pubKey := log.PublicKey()
	log.Close()

	// Verify
	check, records, err := VerifyLog(path, pubKey)
	if err != nil {
		t.Fatal(err)
	}
	if !check.Valid {
		t.Fatalf("log invalid: %v", check.Errors)
	}
	if check.RecordCount != 2 {
		t.Fatalf("expected 2 records, got %d", check.RecordCount)
	}
	if len(records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(records))
	}
}

func TestTamperDetection_RecordModification(t *testing.T) {
	path := tmpLogPath(t)
	log, _ := NewSignedLog(NewLogInput{Path: path, SignerID: "signer"})
	log.Append("PostTask", "t1", "QmA", "aa"+repeat("0", 62), "p1")
	pubKey := log.PublicKey()
	log.Close()

	// Tamper: modify a record
	data, _ := os.ReadFile(path)
	tampered := []byte(repeat("X", 10) + string(data[10:]))
	os.WriteFile(path, tampered, 0644)

	check, _, _ := VerifyLog(path, pubKey)
	if check.Valid {
		t.Fatal("should detect tampering")
	}
}

func TestTamperDetection_RecordDeletion(t *testing.T) {
	path := tmpLogPath(t)
	log, _ := NewSignedLog(NewLogInput{Path: path, SignerID: "signer"})
	log.Append("PostTask", "t1", "QmA", "aa"+repeat("0", 62), "p1")
	log.Append("PostTask", "t2", "QmB", "bb"+repeat("0", 62), "p2")
	pubKey := log.PublicKey()
	checkpoint, err := log.Checkpoint()
	if err != nil {
		t.Fatal(err)
	}
	log.Close()

	// Delete second record
	data, _ := os.ReadFile(path)
	lines := splitJSONLLines(data)
	os.WriteFile(path, lines[0], 0644)
	os.WriteFile(path, append(lines[0], '\n'), 0644)

	check, _, _ := VerifyLogWithCheckpoint(path, pubKey, checkpoint)
	if check.Valid {
		t.Fatal("signed checkpoint did not detect suffix deletion")
	}
}

func TestConcurrentAppend(t *testing.T) {
	path := tmpLogPath(t)
	log, err := NewSignedLog(NewLogInput{Path: path, SignerID: "concurrent-signer"})
	if err != nil {
		t.Fatal(err)
	}
	const count = 100
	var wg sync.WaitGroup
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := log.Append("PostTask", "task", "QmA", "aa"+repeat("0", 62), "payload"); err != nil {
				t.Errorf("Append() error = %v", err)
			}
		}()
	}
	wg.Wait()
	checkpoint, err := log.Checkpoint()
	if err != nil {
		t.Fatal(err)
	}
	pubKey := log.PublicKey()
	if err := log.Close(); err != nil {
		t.Fatal(err)
	}
	check, _, err := VerifyLogWithCheckpoint(path, pubKey, checkpoint)
	if err != nil {
		t.Fatal(err)
	}
	if !check.Valid || check.RecordCount != count {
		t.Fatalf("concurrent log check = %+v", check)
	}
}

func TestTamperDetection_RecordReorder(t *testing.T) {
	path := tmpLogPath(t)
	log, _ := NewSignedLog(NewLogInput{Path: path, SignerID: "signer"})
	log.Append("PostTask", "t1", "QmA", "aa"+repeat("0", 62), "p1")
	log.Append("PostTask", "t2", "QmB", "bb"+repeat("0", 62), "p2")
	pubKey := log.PublicKey()
	log.Close()

	// Swap records
	data, _ := os.ReadFile(path)
	lines := splitJSONLLines(data)
	reordered := append(lines[1], '\n')
	reordered = append(reordered, lines[0]...)
	reordered = append(reordered, '\n')
	os.WriteFile(path, reordered, 0644)

	check, _, _ := VerifyLog(path, pubKey)
	if check.Valid {
		t.Fatal("should detect reordering via hash chain break")
	}
}

func TestTamperDetection_DuplicateTaskSettlement(t *testing.T) {
	path := tmpLogPath(t)
	log, _ := NewSignedLog(NewLogInput{Path: path, SignerID: "signer"})
	log.Append("Settle", "t1", "QmA", "aa"+repeat("0", 62), "settled")
	log.Append("Settle", "t1", "QmA", "aa"+repeat("0", 62), "settled_again")
	pubKey := log.PublicKey()
	log.Close()

	check, records, _ := VerifyLog(path, pubKey)
	if !check.Valid {
		t.Fatalf("duplicate settlement is detectable at app level, not hash: %v", check.Errors)
	}
	// Application-level: check for duplicate task IDs
	seen := make(map[string]bool)
	for _, r := range records {
		if r.Op == "Settle" && seen[r.TaskID] {
			t.Log("Application-level duplicate settlement detected")
		}
		seen[r.TaskID] = true
	}
}

func repeat(s string, n int) string {
	result := ""
	for i := 0; i < n; i++ {
		result += s
	}
	return result
}
