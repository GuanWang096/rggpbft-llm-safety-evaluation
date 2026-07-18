package internal

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func TestArtifactWriterConcurrentRecord(t *testing.T) {
	base := t.TempDir()
	w, err := NewArtifactWriter(base, "concurrent")
	if err != nil {
		t.Fatal(err)
	}

	const count = 100
	var wg sync.WaitGroup
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func(seq int) {
			defer wg.Done()
			if err := w.Record(OpRecord{
				RunID: "concurrent", Seq: seq + 1, Op: "test", TaskID: "task",
				Success: true, LatencyMs: 10, BytesSent: 64, StartedAt: int64(seq),
			}); err != nil {
				t.Errorf("Record() error = %v", err)
			}
		}(i)
	}
	wg.Wait()

	summary, err := w.Summarize()
	if err != nil {
		t.Fatal(err)
	}
	w.Close()
	if summary.TotalOps != count || summary.SuccessOps != count {
		t.Fatalf("summary counts = %d/%d", summary.TotalOps, summary.SuccessOps)
	}

	f, err := os.Open(filepath.Join(base, "concurrent", "operations.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	lines := 0
	for scanner.Scan() {
		var record OpRecord
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			t.Fatalf("invalid JSONL line %d: %v", lines+1, err)
		}
		lines++
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if lines != count {
		t.Fatalf("JSONL lines = %d, want %d", lines, count)
	}
}

func TestSummaryUsesOperationTimeBounds(t *testing.T) {
	w, err := NewArtifactWriter(t.TempDir(), "timing")
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()

	for _, record := range []OpRecord{
		{RunID: "timing", Seq: 2, Op: "b", Success: true, LatencyMs: 100, StartedAt: 1200},
		{RunID: "timing", Seq: 1, Op: "a", Success: true, LatencyMs: 100, StartedAt: 1000},
	} {
		if err := w.Record(record); err != nil {
			t.Fatal(err)
		}
	}

	summary, err := w.Summarize()
	if err != nil {
		t.Fatal(err)
	}
	// The measurement window is [1000, 1300] ms, so 2 operations / 0.3 s.
	if summary.Throughput < 6.66 || summary.Throughput > 6.67 {
		t.Fatalf("throughput = %f, want about 6.667", summary.Throughput)
	}
}
