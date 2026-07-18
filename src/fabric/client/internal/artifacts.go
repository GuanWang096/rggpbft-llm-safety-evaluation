package internal

import (
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

type RunConfig struct {
	RunID       string            `json:"runId"`
	Seed        int64             `json:"seed"`
	Topology    string            `json:"topology"`
	Versions    map[string]string `json:"versions"`
	Concurrency int               `json:"concurrency"`
	PayloadSize int64             `json:"payloadSize"`
	NumTasks    int               `json:"numTasks"`
	StorageMode string            `json:"storageMode,omitempty"`
	StartedAt   time.Time         `json:"startedAt"`
}

type OpRecord struct {
	RunID     string `json:"runId"`
	Seq       int    `json:"seq"`
	Op        string `json:"op"`
	TaskID    string `json:"taskId"`
	Success   bool   `json:"success"`
	ErrorCode string `json:"errorCode,omitempty"`
	LatencyMs int64  `json:"latencyMs"`
	BytesSent int64  `json:"bytesSent"`
	CID       string `json:"cid,omitempty"`
	TxID      string `json:"txId,omitempty"`
	Identity  string `json:"identity"`
	Warmup    bool   `json:"warmup"`
	StartedAt int64  `json:"startedAt"`
}

type BenchmarkSummary struct {
	RunID       string  `json:"runId"`
	TotalOps    int     `json:"totalOps"`
	SuccessOps  int     `json:"successOps"`
	FailureOps  int     `json:"failureOps"`
	SuccessRate float64 `json:"successRate"`
	MedianLatMs float64 `json:"medianLatMs"`
	P50LatMs    float64 `json:"p50LatMs"`
	P95LatMs    float64 `json:"p95LatMs"`
	P99LatMs    float64 `json:"p99LatMs"`
	MinLatMs    int64   `json:"minLatMs"`
	MaxLatMs    int64   `json:"maxLatMs"`
	Throughput  float64 `json:"throughput"`
	BytesTotal  int64   `json:"bytesTotal"`
}

type ArtifactWriter struct {
	mu        sync.Mutex
	dir       string
	runID     string
	jsonlFile *os.File
	csvFile   *os.File
	records   []OpRecord
}

func NewArtifactWriter(baseDir, runID string) (*ArtifactWriter, error) {
	dir := filepath.Join(baseDir, runID)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("create run dir: %w", err)
	}

	jsonlPath := filepath.Join(dir, "operations.jsonl")
	jsonlFile, err := os.Create(jsonlPath)
	if err != nil {
		return nil, fmt.Errorf("create jsonl: %w", err)
	}

	csvPath := filepath.Join(dir, "operations.csv")
	csvFile, err := os.Create(csvPath)
	if err != nil {
		jsonlFile.Close()
		return nil, fmt.Errorf("create csv: %w", err)
	}
	csvWriter := csv.NewWriter(csvFile)
	csvWriter.Write([]string{"seq", "op", "taskId", "success", "errorCode", "latencyMs", "bytesSent", "cid", "txId", "identity", "warmup"})
	csvWriter.Flush()

	return &ArtifactWriter{dir: dir, runID: runID, jsonlFile: jsonlFile, csvFile: csvFile}, nil
}

func (w *ArtifactWriter) WriteConfig(cfg RunConfig) error {
	cfg.Versions = map[string]string{
		"fabric": "2.5.16",
		"ipfs":   "kubo-v0.42.0",
		"go":     "1.22",
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.dir, "config.json"), data, 0644)
}

func (w *ArtifactWriter) WriteEnv() error {
	hostname, _ := os.Hostname()
	env := map[string]string{
		"hostname":  hostname,
		"goVersion": "1.22",
	}
	data, _ := json.MarshalIndent(env, "", "  ")
	return os.WriteFile(filepath.Join(w.dir, "environment.json"), data, 0644)
}

func (w *ArtifactWriter) Record(rec OpRecord) error {
	w.mu.Lock()
	defer w.mu.Unlock()

	w.records = append(w.records, rec)

	line, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if _, err := w.jsonlFile.Write(append(line, '\n')); err != nil {
		return err
	}

	csvWriter := csv.NewWriter(w.csvFile)
	warmupStr := "false"
	if rec.Warmup {
		warmupStr = "true"
	}
	csvWriter.Write([]string{
		fmt.Sprintf("%d", rec.Seq),
		rec.Op,
		rec.TaskID,
		fmt.Sprintf("%t", rec.Success),
		rec.ErrorCode,
		fmt.Sprintf("%d", rec.LatencyMs),
		fmt.Sprintf("%d", rec.BytesSent),
		rec.CID,
		rec.TxID,
		rec.Identity,
		warmupStr,
	})
	csvWriter.Flush()
	return csvWriter.Error()
}

func (w *ArtifactWriter) Summarize() (BenchmarkSummary, error) {
	w.mu.Lock()
	records := append([]OpRecord(nil), w.records...)
	w.mu.Unlock()

	// exclude warm-up records
	var ops []OpRecord
	for _, r := range records {
		if !r.Warmup {
			ops = append(ops, r)
		}
	}
	if len(ops) == 0 {
		return BenchmarkSummary{RunID: w.runID}, nil
	}

	var success int
	var latencies []int64
	var totalBytes int64
	for _, r := range ops {
		totalBytes += r.BytesSent
		if r.Success {
			success++
			latencies = append(latencies, r.LatencyMs)
		}
	}
	sort.Slice(latencies, func(i, j int) bool { return latencies[i] < latencies[j] })

	n := len(latencies)
	s := BenchmarkSummary{
		RunID:       w.runID,
		TotalOps:    len(ops),
		SuccessOps:  success,
		FailureOps:  len(ops) - success,
		SuccessRate: float64(success) / float64(len(ops)),
		BytesTotal:  totalBytes,
	}
	if n > 0 {
		s.MinLatMs = latencies[0]
		s.MaxLatMs = latencies[n-1]
		s.P50LatMs = float64(latencies[n*50/100])
		s.P95LatMs = float64(latencies[n*95/100])
		s.P99LatMs = float64(latencies[n*99/100])

		var sum int64
		for _, l := range latencies {
			sum += l
		}
		if n%2 == 0 {
			s.MedianLatMs = float64(latencies[n/2-1]+latencies[n/2]) / 2.0
		} else {
			s.MedianLatMs = float64(latencies[n/2])
		}
		start := ops[0].StartedAt
		end := ops[0].StartedAt + ops[0].LatencyMs
		for _, op := range ops[1:] {
			if op.StartedAt < start {
				start = op.StartedAt
			}
			if opEnd := op.StartedAt + op.LatencyMs; opEnd > end {
				end = opEnd
			}
		}
		totalSec := float64(end-start) / 1000.0
		if totalSec > 0 {
			s.Throughput = float64(success) / totalSec
		}
	}

	data, _ := json.MarshalIndent(s, "", "  ")
	os.WriteFile(filepath.Join(w.dir, "summary.json"), data, 0644)

	csvPath := filepath.Join(w.dir, "summary.csv")
	f, err := os.Create(csvPath)
	if err != nil {
		return s, err
	}
	defer f.Close()
	w2 := csv.NewWriter(f)
	w2.Write([]string{"runId", "totalOps", "successOps", "failureOps", "successRate",
		"p50LatMs", "p95LatMs", "p99LatMs", "minLatMs", "maxLatMs", "throughput", "bytesTotal"})
	w2.Write([]string{s.RunID,
		fmt.Sprintf("%d", s.TotalOps), fmt.Sprintf("%d", s.SuccessOps), fmt.Sprintf("%d", s.FailureOps),
		fmt.Sprintf("%.4f", s.SuccessRate), fmt.Sprintf("%.2f", s.P50LatMs), fmt.Sprintf("%.2f", s.P95LatMs),
		fmt.Sprintf("%.2f", s.P99LatMs), fmt.Sprintf("%d", s.MinLatMs), fmt.Sprintf("%d", s.MaxLatMs),
		fmt.Sprintf("%.2f", s.Throughput), fmt.Sprintf("%d", s.BytesTotal),
	})
	w2.Flush()
	return s, nil
}

func (w *ArtifactWriter) WriteChecksums() error {
	checksums := map[string]string{}
	for _, name := range []string{"config.json", "environment.json", "operations.jsonl", "operations.csv", "summary.json", "summary.csv"} {
		data, err := os.ReadFile(filepath.Join(w.dir, name))
		if err != nil {
			continue
		}
		h := sha256.Sum256(data)
		checksums[name] = hex.EncodeToString(h[:])
	}
	data, _ := json.MarshalIndent(checksums, "", "  ")
	return os.WriteFile(filepath.Join(w.dir, "checksums.sha256"), data, 0644)
}

func (w *ArtifactWriter) Close() {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.jsonlFile.Close()
	w.csvFile.Close()
}
