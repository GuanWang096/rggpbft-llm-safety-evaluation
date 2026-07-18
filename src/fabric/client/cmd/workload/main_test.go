package main

import (
	"bytes"
	"math/rand"
	"testing"
)

func TestGeneratePayloadUsesExactRequestedSize(t *testing.T) {
	rng1 := rand.New(rand.NewSource(42))
	rng2 := rand.New(rand.NewSource(42))

	got1, err := generatePayload(rng1, "task-1", 1, 1024)
	if err != nil {
		t.Fatal(err)
	}
	got2, err := generatePayload(rng2, "task-1", 1, 1024)
	if err != nil {
		t.Fatal(err)
	}
	if len(got1) != 1024 {
		t.Fatalf("payload length = %d, want 1024", len(got1))
	}
	if !bytes.Equal(got1, got2) {
		t.Fatal("payload is not deterministic for a fixed seed")
	}
}

func TestGeneratePayloadRejectsUndersizedPayload(t *testing.T) {
	_, err := generatePayload(rand.New(rand.NewSource(1)), "long-task-identifier", 1, 8)
	if err == nil {
		t.Fatal("generatePayload() accepted an undersized payload")
	}
}
