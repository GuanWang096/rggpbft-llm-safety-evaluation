package internal

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
	"time"
)

// requiresIPFS skips the test if the local IPFS node is not reachable.
func requiresIPFS(t *testing.T) *Client {
	t.Helper()
	c := NewClient("")
	_, err := c.HTTP.Get(c.API + "/api/v0/id")
	if err != nil {
		t.Skip("IPFS node not available, skipping integration test")
	}
	return c
}

func TestAddEmptyData(t *testing.T) {
	c := requiresIPFS(t)
	_, err := c.Add(nil, "empty.bin")
	if err == nil {
		t.Fatal("expected error for empty data")
	}
	if !strings.Contains(err.Error(), "empty") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestAddAndCatRoundTrip(t *testing.T) {
	c := requiresIPFS(t)
	data := []byte(`{"evidence":"safety-evaluation-result","task":"t1","score":850000}`)

	result, err := c.Add(data, "evidence.json")
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	if result.CID == "" {
		t.Fatal("CID is empty")
	}
	if result.ByteLength != int64(len(data)) {
		t.Fatalf("ByteLength: got %d, want %d", result.ByteLength, len(data))
	}

	// Verify SHA-256
	h := sha256.Sum256(data)
	if result.SHA256 != hex.EncodeToString(h[:]) {
		t.Fatal("SHA256 mismatch in result")
	}

	// Cat and verify content
	retrieved, err := c.Cat(result.CID)
	if err != nil {
		t.Fatalf("Cat: %v", err)
	}
	if string(retrieved) != string(data) {
		t.Fatal("retrieved content differs from original")
	}

	t.Logf("CID: %s, SHA256: %s, Length: %d", result.CID, result.SHA256, result.ByteLength)
}

func TestCatEmptyCID(t *testing.T) {
	c := requiresIPFS(t)
	_, err := c.Cat("")
	if err == nil {
		t.Fatal("expected error for empty CID")
	}
}

func TestCatNonExistent(t *testing.T) {
	c := requiresIPFS(t)
	_, err := c.Cat("QmNonExistentCID123456789")
	if err == nil {
		t.Fatal("expected error for non-existent CID")
	}
}

func TestVerifySuccess(t *testing.T) {
	c := requiresIPFS(t)
	data := []byte("verification test payload")

	result, err := c.Add(data, "verify.bin")
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	if err := c.Verify(result.CID, result.SHA256, result.ByteLength); err != nil {
		t.Fatalf("Verify: %v", err)
	}
}

func TestVerifyLengthMismatch(t *testing.T) {
	c := requiresIPFS(t)
	data := []byte("length test data")

	result, err := c.Add(data, "length.bin")
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	err = c.Verify(result.CID, result.SHA256, result.ByteLength+10)
	if err == nil {
		t.Fatal("expected length mismatch error")
	}
	if !strings.Contains(err.Error(), "length mismatch") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestVerifyDigestMismatch(t *testing.T) {
	c := requiresIPFS(t)
	data := []byte("digest test data")

	result, err := c.Add(data, "digest.bin")
	if err != nil {
		t.Fatalf("Add: %v", err)
	}

	err = c.Verify(result.CID, "aabbccddee"+"00"+strings.Repeat("0", 54), result.ByteLength)
	if err == nil {
		t.Fatal("expected digest mismatch error")
	}
	if !strings.Contains(err.Error(), "sha256 mismatch") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestNoFallbackCID(t *testing.T) {
	c := NewClientWithTimeout("http://127.0.0.1:19999", 5*time.Second)
	_, err := c.Add([]byte("test"), "test.bin")
	if err == nil {
		t.Fatal("expected error when IPFS is unreachable")
	}
	// Ensure no synthetic CID is returned
	if strings.Contains(err.Error(), "Qm") {
		t.Fatal("error message must not contain a synthetic CID")
	}
}
