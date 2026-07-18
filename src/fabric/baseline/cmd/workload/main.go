package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"zte-sci.local/trust-evidence/baseline/internal"
)

var (
	concurrency  = flag.Int("c", 4, "concurrent workflows")
	payloadSize  = flag.Int("s", 65536, "evidence payload size")
	numTasks     = flag.Int("n", 12, "measured workflows")
	warmupTasks  = flag.Int("warmup", 1, "warm-up workflows")
	seed         = flag.Int64("seed", 20260704, "random seed")
	mode         = flag.String("mode", "lifecycle", "ingress or lifecycle")
	output       = flag.String("out", "results", "output directory")
	manifestPath = flag.String("manifest", "", "real evidence batch manifest")
	realPayloads []manifestPayload
)

type manifestPayload struct {
	BatchIndex  int
	RecordCount int
	Filename    string
	Data        []byte
}

type batchManifest struct {
	RecordCount int `json:"record_count"`
	Batches     []struct {
		BatchIndex  int    `json:"batch_index"`
		Filename    string `json:"filename"`
		RecordCount int    `json:"record_count"`
		Bytes       int    `json:"bytes"`
		SHA256      string `json:"sha256"`
	} `json:"batches"`
}

func loadManifestPayloads(path string) ([]manifestPayload, error) {
	manifestBytes, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read manifest: %w", err)
	}
	var manifest batchManifest
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		return nil, fmt.Errorf("decode manifest: %w", err)
	}
	if len(manifest.Batches) == 0 {
		return nil, fmt.Errorf("manifest contains no batches")
	}
	payloads := make([]manifestPayload, 0, len(manifest.Batches))
	recordCount := 0
	for expectedIndex, batch := range manifest.Batches {
		if batch.BatchIndex != expectedIndex {
			return nil, fmt.Errorf("batch indexes must be contiguous: got %d, want %d", batch.BatchIndex, expectedIndex)
		}
		data, err := os.ReadFile(filepath.Join(filepath.Dir(path), batch.Filename))
		if err != nil {
			return nil, fmt.Errorf("read batch %d: %w", batch.BatchIndex, err)
		}
		if len(data) != batch.Bytes {
			return nil, fmt.Errorf("batch %d byte length mismatch", batch.BatchIndex)
		}
		digest := sha256.Sum256(data)
		if hex.EncodeToString(digest[:]) != batch.SHA256 {
			return nil, fmt.Errorf("batch %d SHA-256 mismatch", batch.BatchIndex)
		}
		if batch.RecordCount < 1 {
			return nil, fmt.Errorf("batch %d has invalid record count", batch.BatchIndex)
		}
		payloads = append(payloads, manifestPayload{
			BatchIndex: batch.BatchIndex, RecordCount: batch.RecordCount,
			Filename: batch.Filename, Data: data,
		})
		recordCount += batch.RecordCount
	}
	if recordCount != manifest.RecordCount {
		return nil, fmt.Errorf("manifest record count mismatch: got %d, want %d", recordCount, manifest.RecordCount)
	}
	return payloads, nil
}

type opRecord struct {
	Seq         int64  `json:"seq"`
	RunID       string `json:"run_id"`
	TaskID      string `json:"task_id"`
	Op          string `json:"op"`
	Success     bool   `json:"success"`
	Error       string `json:"error,omitempty"`
	LatencyMS   int64  `json:"latency_ms"`
	StartedAtMS int64  `json:"started_at_ms"`
	BytesSent   int64  `json:"bytes_sent"`
	CID         string `json:"cid,omitempty"`
	Warmup      bool   `json:"warmup"`
}

type recorder struct {
	mu      sync.Mutex
	seq     atomic.Int64
	records []opRecord
	status  map[string]bool
}

func (r *recorder) add(record opRecord) {
	record.Seq = r.seq.Add(1)
	r.mu.Lock()
	r.records = append(r.records, record)
	r.mu.Unlock()
}

func (r *recorder) setStatus(taskID string, success bool) {
	r.mu.Lock()
	r.status[taskID] = success
	r.mu.Unlock()
}

func main() {
	flag.Parse()
	if *mode != "ingress" && *mode != "lifecycle" {
		fatalf("invalid mode %q", *mode)
	}
	if *manifestPath != "" {
		var err error
		realPayloads, err = loadManifestPayloads(*manifestPath)
		if err != nil {
			fatalf("load real evidence manifest: %v", err)
		}
		*numTasks = len(realPayloads)
	}
	payloadLabel := fmt.Sprintf("s%d", *payloadSize)
	if len(realPayloads) > 0 {
		payloadLabel = "real"
	}
	runID := fmt.Sprintf("e6-%s-%s-c%d-%s", *mode, time.Now().UTC().Format("20060102T150405.000000000Z"), *concurrency, payloadLabel)
	runDir := filepath.Join(*output, runID)
	if err := os.MkdirAll(runDir, 0755); err != nil {
		fatalf("create run directory: %v", err)
	}

	rec := &recorder{status: make(map[string]bool)}
	warmupLog, err := internal.NewSignedLog(internal.NewLogInput{Path: filepath.Join(runDir, "warmup_log.jsonl"), SignerID: "e6-local-baseline"})
	if err != nil {
		fatalf("create warmup log: %v", err)
	}
	for i := 0; i < *warmupTasks; i++ {
		runWorkflow(rec, warmupLog, runID, i, true)
	}
	warmupLog.Close()

	logPath := filepath.Join(runDir, "signed_log.jsonl")
	signedLog, err := internal.NewSignedLog(internal.NewLogInput{Path: logPath, SignerID: "e6-local-baseline"})
	if err != nil {
		fatalf("create measured log: %v", err)
	}
	sem := make(chan struct{}, *concurrency)
	var wg sync.WaitGroup
	for i := 0; i < *numTasks; i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			runWorkflow(rec, signedLog, runID, index, false)
		}(i)
	}
	wg.Wait()
	checkpoint, err := signedLog.Checkpoint()
	if err != nil {
		fatalf("checkpoint log: %v", err)
	}
	publicKey := signedLog.PublicKey()
	if err := signedLog.Close(); err != nil {
		fatalf("close log: %v", err)
	}

	check, _, err := internal.VerifyLogWithCheckpoint(logPath, publicKey, checkpoint)
	if err != nil || !check.Valid {
		fatalf("verify measured log: %v, %+v", err, check)
	}
	writeJSON(filepath.Join(runDir, "checkpoint.json"), checkpoint)
	if err := os.WriteFile(filepath.Join(runDir, "public_key.hex"), []byte(hex.EncodeToString(publicKey)+"\n"), 0644); err != nil {
		fatalf("write public key: %v", err)
	}
	tamper := runTamperChecks(runDir, logPath, publicKey, checkpoint)
	writeJSON(filepath.Join(runDir, "tamper_results.json"), tamper)
	writeArtifacts(runDir, runID, rec, checkpoint)
	fmt.Printf("E6 %s completed: %s\n", *mode, runDir)
}

func runWorkflow(rec *recorder, log *internal.SignedLog, runID string, index int, warmup bool) {
	taskID := fmt.Sprintf("%s-%s-%04d", runID, map[bool]string{true: "warmup", false: "task"}[warmup], index)
	var payload []byte
	if len(realPayloads) > 0 {
		payload = realPayloads[index%len(realPayloads)].Data
	} else {
		rng := rand.New(rand.NewSource(*seed + int64(index) + map[bool]int64{true: 1_000_000}[warmup]))
		var err error
		payload, err = generatePayload(rng, taskID, index, *payloadSize)
		if err != nil {
			rec.setStatus(taskID, false)
			return
		}
	}
	digest := sha256.Sum256(payload)
	shaHex := hex.EncodeToString(digest[:])
	success := false
	defer func() { rec.setStatus(taskID, success) }()

	cid, err := measure(rec, runID, taskID, "ipfs_add", warmup, int64(len(payload)), "", func() (string, error) {
		return ipfsAdd(payload)
	})
	if err != nil {
		return
	}
	operations := []string{"PostTask"}
	if *mode == "lifecycle" {
		operations = append(operations, "PostAllocation", "PostEvalSnapshot", "Vote1", "Vote2", "Settlement")
	}
	for _, operation := range operations {
		op := operation
		_, err = measure(rec, runID, taskID, "signed_"+toSnake(op), warmup, 0, cid, func() (internal.Record, error) {
			return log.Append(op, taskID, cid, shaHex, `{"state":"committed"}`)
		})
		if err != nil {
			return
		}
	}
	_, err = measure(rec, runID, taskID, "signed_query_latest", warmup, 0, cid, func() (internal.Record, error) {
		record, found := log.Latest(taskID)
		if !found || record.TaskID != taskID || record.CID != cid || record.SHA256 != shaHex {
			return internal.Record{}, fmt.Errorf("latest record mismatch")
		}
		return record, nil
	})
	if err != nil {
		return
	}
	_, err = measure(rec, runID, taskID, "ipfs_verify", warmup, 0, cid, func() (struct{}, error) {
		return struct{}{}, ipfsVerify(cid, shaHex, len(payload))
	})
	if err == nil {
		success = true
	}
}

func measure[T any](rec *recorder, runID, taskID, op string, warmup bool, bytesSent int64, cid string, fn func() (T, error)) (T, error) {
	started := time.Now().UnixMilli()
	value, err := fn()
	elapsed := time.Now().UnixMilli() - started
	record := opRecord{RunID: runID, TaskID: taskID, Op: op, Success: err == nil, LatencyMS: elapsed, StartedAtMS: started, BytesSent: bytesSent, CID: cid, Warmup: warmup}
	if err != nil {
		record.Error = err.Error()
	}
	rec.add(record)
	return value, err
}

func generatePayload(rng *rand.Rand, taskID string, sequence, size int) ([]byte, error) {
	encodedID, _ := json.Marshal(taskID)
	prefix := []byte(fmt.Sprintf(`{"taskId":%s,"sequence":%d,"evidence":"`, encodedID, sequence))
	suffix := []byte(`"}`)
	if size < len(prefix)+len(suffix) {
		return nil, fmt.Errorf("payload size %d is too small", size)
	}
	alphabet := "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
	data := make([]byte, size)
	copy(data, prefix)
	for i := len(prefix); i < size-len(suffix); i++ {
		data[i] = alphabet[rng.Intn(len(alphabet))]
	}
	copy(data[size-len(suffix):], suffix)
	return data, nil
}

func ipfsAdd(payload []byte) (string, error) {
	cmd := exec.Command("curl", "--fail", "--silent", "--show-error", "-X", "POST", "-F", "file=@-;filename=evidence.json", "http://localhost:5001/api/v0/add")
	cmd.Stdin = strings.NewReader(string(payload))
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("ipfs add: %w: %s", err, out)
	}
	var response struct {
		Hash string `json:"Hash"`
	}
	if err := json.Unmarshal(out, &response); err != nil || response.Hash == "" {
		return "", fmt.Errorf("decode IPFS add response: %w", err)
	}
	return response.Hash, nil
}

func ipfsVerify(cid, expectedSHA string, expectedSize int) error {
	out, err := exec.Command("curl", "--fail", "--silent", "--show-error", "-X", "POST", "http://localhost:5001/api/v0/cat?arg="+cid).CombinedOutput()
	if err != nil {
		return err
	}
	digest := sha256.Sum256(out)
	if len(out) != expectedSize || hex.EncodeToString(digest[:]) != expectedSHA {
		return fmt.Errorf("IPFS integrity mismatch")
	}
	return nil
}

func runTamperChecks(runDir, source string, publicKey ed25519.PublicKey, checkpoint internal.Checkpoint) map[string]bool {
	data, err := os.ReadFile(source)
	if err != nil {
		fatalf("read log for tamper tests: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	variants := map[string][]string{}
	modified := append([]string(nil), lines...)
	modified[0] = strings.Replace(modified[0], "PostTask", "XostTask", 1)
	variants["record_modification"] = modified
	variants["suffix_deletion"] = append([]string(nil), lines[:len(lines)-1]...)
	reordered := append([]string(nil), lines...)
	if len(reordered) > 1 {
		reordered[0], reordered[1] = reordered[1], reordered[0]
	}
	variants["record_reordering"] = reordered
	results := map[string]bool{}
	for name, variant := range variants {
		path := filepath.Join(runDir, "tampered_"+name+".jsonl")
		if err := os.WriteFile(path, []byte(strings.Join(variant, "\n")+"\n"), 0644); err != nil {
			fatalf("write tamper variant: %v", err)
		}
		check, _, verifyErr := internal.VerifyLogWithCheckpoint(path, publicKey, checkpoint)
		results[name+"_detected"] = verifyErr != nil || !check.Valid
	}
	return results
}

func writeArtifacts(runDir, runID string, rec *recorder, checkpoint internal.Checkpoint) {
	rec.mu.Lock()
	records := append([]opRecord(nil), rec.records...)
	statuses := make(map[string]bool, len(rec.status))
	for key, value := range rec.status {
		statuses[key] = value
	}
	rec.mu.Unlock()
	sort.Slice(records, func(i, j int) bool { return records[i].Seq < records[j].Seq })
	measured := make([]opRecord, 0)
	for _, record := range records {
		if !record.Warmup {
			measured = append(measured, record)
		}
	}
	measuredTasks := map[string]bool{}
	for _, record := range measured {
		measuredTasks[record.TaskID] = statuses[record.TaskID]
	}
	start, end := measured[0].StartedAtMS, measured[0].StartedAtMS+measured[0].LatencyMS
	for _, record := range measured[1:] {
		if record.StartedAtMS < start {
			start = record.StartedAtMS
		}
		if record.StartedAtMS+record.LatencyMS > end {
			end = record.StartedAtMS + record.LatencyMS
		}
	}
	successfulWorkflows := 0
	for _, success := range measuredTasks {
		if success {
			successfulWorkflows++
		}
	}
	perOp := map[string][]int64{}
	perOpCounts := map[string]int{}
	perOpFailures := map[string]int{}
	for _, record := range measured {
		perOpCounts[record.Op]++
		if record.Success {
			perOp[record.Op] = append(perOp[record.Op], record.LatencyMS)
		} else {
			perOpFailures[record.Op]++
		}
	}
	perOpSummary := map[string]map[string]any{}
	for op, values := range perOp {
		sort.Slice(values, func(i, j int) bool { return values[i] < values[j] })
		perOpSummary[op] = map[string]any{"count": perOpCounts[op], "failure_count": perOpFailures[op], "p50_ms": pct(values, .5), "p95_ms": pct(values, .95), "p99_ms": pct(values, .99)}
	}
	duration := end - start
	summary := map[string]any{
		"operation_count": len(measured), "successful_operations": countSuccessful(measured), "failed_operations": len(measured) - countSuccessful(measured), "workflow_count": len(measuredTasks),
		"successful_workflows": successfulWorkflows, "failed_workflows": len(measuredTasks) - successfulWorkflows,
		"workflow_success_rate":   float64(successfulWorkflows) / float64(len(measuredTasks)),
		"measurement_duration_ms": duration, "workflow_throughput_per_s": float64(successfulWorkflows) / (float64(duration) / 1000),
		"log_record_count": checkpoint.RecordCount, "log_bytes": fileSize(filepath.Join(runDir, "signed_log.jsonl")), "per_operation": perOpSummary,
	}
	if len(realPayloads) > 0 {
		evidenceRecords := 0
		for _, payload := range realPayloads {
			evidenceRecords += payload.RecordCount
		}
		summary["evidence_record_count"] = evidenceRecords
		summary["evidence_record_throughput_per_s"] = float64(evidenceRecords) / (float64(duration) / 1000)
	}
	config := map[string]any{"run_id": runID, "mode": *mode, "seed": *seed, "concurrency": *concurrency, "payload_size": *payloadSize, "tasks": *numTasks, "warmup_tasks": *warmupTasks, "manifest": *manifestPath, "payload_mode": map[bool]string{true: "real-e1-batches", false: "synthetic-fixed-size"}[len(realPayloads) > 0], "durability": "fsync after every signed append", "trust_model": "single writer; no distributed endorsement or consensus"}
	writeJSON(filepath.Join(runDir, "config.json"), config)
	writeJSON(filepath.Join(runDir, "environment.json"), map[string]any{"go_version": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH})
	writeJSON(filepath.Join(runDir, "summary.json"), summary)
	jsonl, err := os.Create(filepath.Join(runDir, "operations.jsonl"))
	if err != nil {
		fatalf("create operations: %v", err)
	}
	csvFile, err := os.Create(filepath.Join(runDir, "operations.csv"))
	if err != nil {
		fatalf("create CSV: %v", err)
	}
	csvWriter := csv.NewWriter(csvFile)
	csvWriter.Write([]string{"seq", "task_id", "op", "success", "latency_ms", "started_at_ms", "bytes_sent", "cid", "warmup"})
	for _, record := range records {
		encoded, _ := json.Marshal(record)
		jsonl.Write(append(encoded, '\n'))
		csvWriter.Write([]string{fmt.Sprint(record.Seq), record.TaskID, record.Op, fmt.Sprint(record.Success), fmt.Sprint(record.LatencyMS), fmt.Sprint(record.StartedAtMS), fmt.Sprint(record.BytesSent), record.CID, fmt.Sprint(record.Warmup)})
	}
	csvWriter.Flush()
	csvFile.Close()
	jsonl.Close()
	files := []string{"config.json", "environment.json", "summary.json", "operations.jsonl", "operations.csv", "signed_log.jsonl", "checkpoint.json", "public_key.hex", "tamper_results.json"}
	manifest, err := os.Create(filepath.Join(runDir, "checksums.sha256"))
	if err != nil {
		fatalf("create checksums: %v", err)
	}
	for _, name := range files {
		data, _ := os.ReadFile(filepath.Join(runDir, name))
		sum := sha256.Sum256(data)
		fmt.Fprintf(manifest, "%s  %s\n", hex.EncodeToString(sum[:]), name)
	}
	manifest.Close()
}

func pct(values []int64, fraction float64) int64 { return values[int(fraction*float64(len(values)-1))] }
func countSuccessful(records []opRecord) int {
	count := 0
	for _, record := range records {
		if record.Success {
			count++
		}
	}
	return count
}
func toSnake(value string) string { return strings.ToLower(strings.ReplaceAll(value, "Eval", "_eval")) }
func fileSize(path string) int64 {
	info, err := os.Stat(path)
	if err != nil {
		return 0
	}
	return info.Size()
}
func writeJSON(path string, value any) {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		fatalf("encode JSON: %v", err)
	}
	if err := os.WriteFile(path, append(data, '\n'), 0644); err != nil {
		fatalf("write JSON: %v", err)
	}
}
func fatalf(format string, args ...any) { fmt.Fprintf(os.Stderr, format+"\n", args...); os.Exit(1) }
