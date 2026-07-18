package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"zte-sci.local/trust-evidence/client/internal"
)

var (
	concurrency = flag.Int("c", 10, "concurrent workers")
	payloadSize = flag.Int("s", 10240, "evidence payload size in bytes")
	numTasks    = flag.Int("n", 50, "number of tasks")
	warmupTasks = flag.Int("warmup", 5, "warm-up tasks (excluded from metrics)")
	seed        = flag.Int64("seed", 42, "random seed")
	runDir      = flag.String("out", "results", "output directory for run artifacts")
	storageMode = flag.String("storage", "hybrid", "hybrid or inline evidence storage")
	peerBinary  = flag.String("peer", "", "path to peer binary")
	tdir        = flag.String("tdir", "", "test-network directory")
)

func main() {
	flag.Parse()
	log.SetFlags(log.Ltime)
	if *storageMode != "hybrid" && *storageMode != "inline" {
		log.Fatalf("unsupported storage mode %q", *storageMode)
	}

	rng := rand.New(rand.NewSource(*seed))
	runID := fmt.Sprintf("e4-ingress-%s-c%d-s%d", time.Now().UTC().Format("20060102T150405.000000000Z"), *concurrency, *payloadSize)
	baseDir := filepath.Join(*runDir, runID)

	aw, err := internal.NewArtifactWriter(*runDir, runID)
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		aw.Summarize()
		aw.WriteChecksums()
		aw.Close()
	}()

	aw.WriteConfig(internal.RunConfig{
		RunID: runID, Seed: *seed,
		Topology:    "3org-1peer-1orderer-single-host",
		Concurrency: *concurrency, PayloadSize: int64(*payloadSize),
		NumTasks: *numTasks, StartedAt: time.Now(),
		StorageMode: *storageMode,
	})
	aw.WriteEnv()

	peerPath := resolvePeer(*peerBinary, *tdir)
	testNetDir := *tdir
	if testNetDir == "" {
		testNetDir = findTestNetwork()
	}

	log.Printf("E4 benchmark: c=%d payload=%d tasks=%d warmup=%d out=%s",
		*concurrency, *payloadSize, *numTasks, *warmupTasks, baseDir)
	log.Printf("Peer: %s  TestNetwork: %s", peerPath, testNetDir)

	type task struct {
		id     string
		data   []byte
		sha256 string
		cid    string
	}

	tasks := make([]task, *numTasks+*warmupTasks)
	for i := 0; i < len(tasks); i++ {
		taskID := fmt.Sprintf("%s-task-%04d", runID, i)
		data, err := generatePayload(rng, taskID, i, *payloadSize)
		if err != nil {
			log.Fatal(err)
		}
		h := sha256.Sum256(data)
		tasks[i] = task{
			id:     taskID,
			data:   data,
			sha256: hex.EncodeToString(h[:]),
		}
	}

	var seq atomic.Int64
	record := func(op, taskID, errCode string, latencyMs, startedAt, bytesSent int64, success bool, cid string, warmup bool) {
		recordSeq := seq.Add(1)
		if err := aw.Record(internal.OpRecord{
			RunID: runID, Seq: int(recordSeq), Op: op, TaskID: taskID,
			Success: success, ErrorCode: errCode, LatencyMs: latencyMs,
			BytesSent: bytesSent,
			CID:       cid, Identity: "benchmark-driver", Warmup: warmup,
			StartedAt: startedAt,
		}); err != nil {
			log.Printf("record artifact: %v", err)
		}
	}

	runTask := func(t *task, warmup bool) {
		cid := "inline"
		var err error
		if *storageMode == "hybrid" {
			startedAt := time.Now().UnixMilli()
			t0 := time.Now()
			cid, err = ipfsAdd(t.data)
			record("ipfs_add", t.id, errStr(err), time.Since(t0).Milliseconds(), startedAt, int64(len(t.data)), err == nil, cid, warmup)
			if err != nil {
				return
			}
		}
		t.cid = cid

		// Fabric PostTask
		startedAt := time.Now().UnixMilli()
		t0 := time.Now()
		inlinePayload := ""
		if *storageMode == "inline" {
			inlinePayload = string(t.data)
		}
		err = fabricPostTask(peerPath, testNetDir, t.id, cid, t.sha256, int64(len(t.data)), inlinePayload)
		record("fabric_post_task", t.id, errStr(err), time.Since(t0).Milliseconds(), startedAt, int64(len(t.data)), err == nil, cid, warmup)
		if err != nil {
			return
		}

		// Fabric QueryTask
		startedAt = time.Now().UnixMilli()
		t0 = time.Now()
		err = fabricQueryTask(peerPath, testNetDir, t.id, cid, t.sha256, inlinePayload)
		record("fabric_query_task", t.id, errStr(err), time.Since(t0).Milliseconds(), startedAt, 0, err == nil, cid, warmup)

		if *storageMode == "hybrid" {
			startedAt = time.Now().UnixMilli()
			t0 = time.Now()
			err = ipfsVerify(cid, t.sha256, int64(len(t.data)))
			record("ipfs_verify", t.id, errStr(err), time.Since(t0).Milliseconds(), startedAt, 0, err == nil, cid, warmup)
		}
	}

	log.Printf("Warm-up: %d complete ingress workflows", *warmupTasks)
	for i := 0; i < *warmupTasks; i++ {
		runTask(&tasks[i], true)
	}

	log.Printf("Measurement phase: %d tasks, concurrency=%d", *numTasks, *concurrency)
	sem := make(chan struct{}, *concurrency)
	var wg sync.WaitGroup

	for i := 0; i < *numTasks; i++ {
		wg.Add(1)
		go func(t *task) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			runTask(t, false)
		}(&tasks[*warmupTasks+i])
	}
	wg.Wait()

	sum, _ := aw.Summarize()
	log.Printf("Done: %+v", sum)
	log.Printf("Results: %s", filepath.Join(baseDir, "summary.json"))
}

func generatePayload(rng *rand.Rand, taskID string, seq, size int) ([]byte, error) {
	encodedTaskID, err := json.Marshal(taskID)
	if err != nil {
		return nil, fmt.Errorf("encode task ID: %w", err)
	}
	prefix := []byte(fmt.Sprintf(`{"taskId":%s,"seq":%d,"evidence":"`, encodedTaskID, seq))
	suffix := []byte(`"}`)
	if size < len(prefix)+len(suffix) {
		return nil, fmt.Errorf("payload size %d is smaller than metadata envelope %d", size, len(prefix)+len(suffix))
	}
	data := make([]byte, size)
	copy(data, prefix)
	alphabet := "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
	for i := len(prefix); i < size-len(suffix); i++ {
		data[i] = alphabet[rng.Intn(len(alphabet))]
	}
	copy(data[size-len(suffix):], suffix)
	return data, nil
}

func ipfsAdd(data []byte) (string, error) {
	cmd := exec.Command("curl", "-s", "-X", "POST",
		"-F", "file=@-;filename=evidence.json",
		"http://localhost:5001/api/v0/add")
	cmd.Stdin = strings.NewReader(string(data))
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("ipfs add: %w", err)
	}
	var resp struct {
		Hash string `json:"Hash"`
	}
	if err := json.Unmarshal(out, &resp); err != nil {
		return "", fmt.Errorf("parse ipfs response: %w, body=%s", err, string(out[:min(200, len(out))]))
	}
	if resp.Hash == "" {
		return "", fmt.Errorf("empty CID")
	}
	return resp.Hash, nil
}

func ipfsVerify(cid, expectedSHA256 string, expectedLen int64) error {
	cmd := exec.Command("curl", "-s", "-X", "POST",
		"http://localhost:5001/api/v0/cat?arg="+cid)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("cat: %w", err)
	}
	if int64(len(out)) != expectedLen {
		return fmt.Errorf("length mismatch: %d vs %d", len(out), expectedLen)
	}
	h := sha256.Sum256(out)
	if hex.EncodeToString(h[:]) != expectedSHA256 {
		return fmt.Errorf("sha256 mismatch")
	}
	return nil
}

func fabricPostTask(peerPath, testNetDir, taskID, cid, sha256Hex string, inputBytes int64, inlinePayload string) error {
	input := map[string]any{
		"taskId": taskID, "subjectId": "s1", "riskCategories": []string{"multimodal-safety"},
		"modalities": []string{"text", "image"}, "workload": 100, "deadlineUnix": int64(2800000000),
		"inputBytes": inputBytes, "priority": 5, "minEvaluators": 2, "minReputationPpm": 200000,
		"cid": cid, "sha256": sha256Hex,
	}
	if inlinePayload != "" {
		input["inlinePayload"] = inlinePayload
	}
	inputBytesJSON, _ := json.Marshal(input)
	inputJSON := string(inputBytesJSON)
	argsJSON, _ := json.Marshal(inputJSON)
	callJSON := fmt.Sprintf(`{"Args":["PostTaskConstraint",%s]}`, string(argsJSON))

	cmd := exec.Command(peerPath, "chaincode", "invoke",
		"-o", "localhost:7050",
		"--ordererTLSHostnameOverride", "orderer.example.com",
		"--tls",
		"--cafile", filepath.Join(testNetDir, "organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"),
		"-C", "trustchannel", "-n", "tce",
		"--peerAddresses", "localhost:7051",
		"--tlsRootCertFiles", filepath.Join(testNetDir, "organizations/peerOrganizations/org1.example.com/tlsca/tlsca.org1.example.com-cert.pem"),
		"--peerAddresses", "localhost:9051",
		"--tlsRootCertFiles", filepath.Join(testNetDir, "organizations/peerOrganizations/org2.example.com/tlsca/tlsca.org2.example.com-cert.pem"),
		"--peerAddresses", "localhost:11051",
		"--tlsRootCertFiles", filepath.Join(testNetDir, "organizations/peerOrganizations/org3.example.com/tlsca/tlsca.org3.example.com-cert.pem"),
		"--waitForEvent", "--waitForEventTimeout", "60s",
		"-c", callJSON,
	)
	cmd.Env = append(os.Environ(),
		"CORE_PEER_TLS_ENABLED=true",
		"CORE_PEER_LOCALMSPID=Org1MSP",
		"CORE_PEER_TLS_ROOTCERT_FILE="+filepath.Join(testNetDir, "organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"),
		"CORE_PEER_MSPCONFIGPATH="+filepath.Join(testNetDir, "organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp"),
		"CORE_PEER_ADDRESS=localhost:7051",
		"FABRIC_CFG_PATH="+filepath.Join(testNetDir, "../config"),
	)
	out, err := cmd.CombinedOutput()
	outStr := string(out)
	if err != nil || !strings.Contains(outStr, "status:200") {
		return fmt.Errorf("postTask failed: %w, output=%s", err, outStr[:min(200, len(outStr))])
	}
	return nil
}

func fabricQueryTask(peerPath, testNetDir, taskID, expectedCID, expectedSHA256, expectedInline string) error {
	cmd := exec.Command(peerPath, "chaincode", "query",
		"-o", "localhost:7050",
		"--ordererTLSHostnameOverride", "orderer.example.com",
		"--tls",
		"--cafile", filepath.Join(testNetDir, "organizations/ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"),
		"-C", "trustchannel", "-n", "tce",
		"-c", fmt.Sprintf(`{"Args":["QueryTask","%s"]}`, taskID),
	)
	cmd.Env = append(os.Environ(),
		"CORE_PEER_TLS_ENABLED=true",
		"CORE_PEER_LOCALMSPID=Org1MSP",
		"CORE_PEER_TLS_ROOTCERT_FILE="+filepath.Join(testNetDir, "organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"),
		"CORE_PEER_MSPCONFIGPATH="+filepath.Join(testNetDir, "organizations/peerOrganizations/org1.example.com/users/Admin@org1.example.com/msp"),
		"CORE_PEER_ADDRESS=localhost:7051",
		"FABRIC_CFG_PATH="+filepath.Join(testNetDir, "../config"),
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("query: %w, output=%s", err, string(out[:min(200, len(out))]))
	}
	var task struct {
		TaskID string `json:"taskId"`
		CID    string `json:"cid"`
		SHA256 string `json:"sha256"`
		Inline string `json:"inlinePayload"`
	}
	if err := json.Unmarshal(out, &task); err != nil {
		return fmt.Errorf("decode query response: %w", err)
	}
	if task.TaskID != taskID || task.CID != expectedCID || task.SHA256 != expectedSHA256 || task.Inline != expectedInline {
		return fmt.Errorf("query response mismatch")
	}
	return nil
}

func resolvePeer(explicitPath, testNetDir string) string {
	if explicitPath != "" {
		return explicitPath
	}
	if testNetDir != "" {
		return filepath.Join(testNetDir, "../bin/peer")
	}
	return findTestNetwork() + "/../bin/peer"
}

func findTestNetwork() string {
	candidates := []string{
		"/mnt/c/Users/guan/Desktop/zte-sci/experiments/fabric/fabric-samples/test-network",
	}
	for _, c := range candidates {
		if fi, err := os.Stat(c); err == nil && fi.IsDir() {
			return c
		}
	}
	return ""
}

func errStr(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
