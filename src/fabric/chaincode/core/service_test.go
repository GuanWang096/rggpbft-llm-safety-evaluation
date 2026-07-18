package core_test

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"zte-sci.local/trust-evidence/chaincode/core"
)

type memoryTx struct {
	state  map[string][]byte
	caller core.Caller
	now    int64
}

func newMemoryTx() *memoryTx {
	return &memoryTx{
		state: map[string][]byte{},
		caller: core.Caller{
			ClientID: "client-1",
			MSPID:    "Org1MSP",
			Attributes: map[string]string{
				"role": "evaluator",
			},
		},
		now: 1_700_000_000,
	}
}

func (tx *memoryTx) Get(key string, out any) (bool, error) {
	value, ok := tx.state[key]
	if !ok {
		return false, nil
	}
	return true, json.Unmarshal(value, out)
}

func (tx *memoryTx) Put(key string, value any) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	tx.state[key] = encoded
	return nil
}

func (tx *memoryTx) Delete(key string) error {
	delete(tx.state, key)
	return nil
}

func (tx *memoryTx) Caller() core.Caller { return tx.caller }

func (tx *memoryTx) TimestampUnix() int64 { return tx.now }

func (tx *memoryTx) snapshot() map[string]string {
	result := make(map[string]string, len(tx.state))
	for key, value := range tx.state {
		result[key] = string(value)
	}
	return result
}

func requireCode(t *testing.T, err error, code string) {
	t.Helper()
	if err == nil || !strings.Contains(err.Error(), code) {
		t.Fatalf("error = %v, want code %s", err, code)
	}
}

func TestSharesMustUsePPM(t *testing.T) {
	members := []core.AllocationMember{
		{EvalID: "e1", SharePPM: 500_000},
		{EvalID: "e2", SharePPM: 500_000},
	}

	if got := core.SumShares(members); got != core.OnePPM {
		t.Fatalf("SumShares() = %d, want %d", got, core.OnePPM)
	}
}

func TestStateKeysAreNamespaced(t *testing.T) {
	if got := core.TaskKey("t1"); got != "TaskConstraint::t1" {
		t.Fatalf("TaskKey() = %q", got)
	}
}

func TestRegisterEvaluator(t *testing.T) {
	tx := newMemoryTx()
	service := core.Service{}
	input := core.RegisterEvaluatorInput{
		EvalID:       "eval-1",
		Capabilities: []string{"vision", "text"},
	}

	if err := service.RegisterEvaluator(tx, input); err != nil {
		t.Fatalf("RegisterEvaluator() error = %v", err)
	}

	var got core.EvaluatorState
	found, err := tx.Get(core.EvaluatorKey(input.EvalID), &got)
	if err != nil || !found {
		t.Fatalf("evaluator lookup found=%v error=%v", found, err)
	}
	if got.AlphaMicro != core.OnePPM || got.BetaMicro != core.OnePPM {
		t.Fatalf("Beta prior = (%d, %d)", got.AlphaMicro, got.BetaMicro)
	}
	if got.ReputationPPM != core.OnePPM/2 {
		t.Fatalf("reputation = %d", got.ReputationPPM)
	}
	if !reflect.DeepEqual(got.Capabilities, input.Capabilities) {
		t.Fatalf("capabilities = %#v", got.Capabilities)
	}

	before := tx.snapshot()
	requireCode(t, service.RegisterEvaluator(tx, input), "ERR_EVALUATOR_EXISTS")
	if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
		t.Fatalf("duplicate registration changed state")
	}
}

func TestPostTaskConstraint(t *testing.T) {
	service := core.Service{}
	valid := core.PostTaskInput{
		TaskID:           "task-1",
		SubjectID:        "model-1",
		RiskCategories:   []string{"unsafe-content"},
		Modalities:       []string{"text"},
		Workload:         4,
		DeadlineUnix:     1_700_003_600,
		InputBytes:       1024,
		Priority:         1,
		MinEvaluators:    3,
		MinReputationPPM: 400_000,
		CID:              "bafybeigdyrzt",
		SHA256:           strings.Repeat("a", 64),
	}

	tests := []struct {
		name   string
		mutate func(*core.PostTaskInput)
		code   string
	}{
		{name: "empty CID", mutate: func(in *core.PostTaskInput) { in.CID = "" }, code: "ERR_INVALID_CID"},
		{name: "invalid hash", mutate: func(in *core.PostTaskInput) { in.SHA256 = "abc" }, code: "ERR_INVALID_HASH"},
		{name: "invalid minimum", mutate: func(in *core.PostTaskInput) { in.MinEvaluators = 0 }, code: "ERR_INVALID_MIN_EVALUATORS"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			tx := newMemoryTx()
			input := valid
			test.mutate(&input)
			before := tx.snapshot()
			requireCode(t, service.PostTaskConstraint(tx, input), test.code)
			if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
				t.Fatalf("rejected task changed state")
			}
		})
	}

	tx := newMemoryTx()
	if err := service.PostTaskConstraint(tx, valid); err != nil {
		t.Fatalf("PostTaskConstraint() error = %v", err)
	}
	task, err := service.QueryTask(tx, valid.TaskID)
	if err != nil {
		t.Fatalf("QueryTask() error = %v", err)
	}
	if task.CreatorClientID != tx.caller.ClientID || task.CreatedAtUnix != tx.now {
		t.Fatalf("creator or timestamp not recorded: %#v", task)
	}

	before := tx.snapshot()
	requireCode(t, service.PostTaskConstraint(tx, valid), "ERR_TASK_EXISTS")
	if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
		t.Fatalf("duplicate task changed state")
	}
}

func TestPostTaskConstraintInlineEvidence(t *testing.T) {
	service := core.Service{}
	payload := "inline evidence"
	digest := sha256.Sum256([]byte(payload))
	input := core.PostTaskInput{
		TaskID: "inline-task", SubjectID: "model-1", MinEvaluators: 1,
		CID: "inline", SHA256: fmt.Sprintf("%x", digest),
		InputBytes: int64(len(payload)), InlinePayload: payload,
	}
	tx := newMemoryTx()
	if err := service.PostTaskConstraint(tx, input); err != nil {
		t.Fatal(err)
	}
	stored, err := service.QueryTask(tx, input.TaskID)
	if err != nil || stored.InlinePayload != payload {
		t.Fatalf("stored inline payload = %q, error = %v", stored.InlinePayload, err)
	}

	bad := input
	bad.TaskID = "bad-inline-task"
	bad.SHA256 = strings.Repeat("0", 64)
	requireCode(t, service.PostTaskConstraint(newMemoryTx(), bad), "ERR_EVIDENCE_HASH_MISMATCH")
}

func TestQueryLifecycleState(t *testing.T) {
	tx := newMemoryTx()
	service := core.Service{}

	allocation := core.Allocation{TaskID: "task-1", Status: "Active"}
	confirmation := core.Confirmation{TaskID: "task-1", Digest: strings.Repeat("a", 64), Status: "Accept"}
	evaluator := core.EvaluatorState{EvalID: "eval-1", ReputationPPM: 750_000}
	reputation := core.TaskReputation{SubjectID: "model-1", TaskID: "task-1", ReputationPPM: 800_000}

	for key, value := range map[string]any{
		core.AllocationKey("task-1"):                allocation,
		core.ConfirmationKey("task-1"):              confirmation,
		core.EvaluatorKey("eval-1"):                 evaluator,
		core.TaskReputationKey("model-1", "task-1"): reputation,
	} {
		if err := tx.Put(key, value); err != nil {
			t.Fatal(err)
		}
	}

	if got, err := service.QueryAllocation(tx, "task-1"); err != nil || !reflect.DeepEqual(got, allocation) {
		t.Fatalf("QueryAllocation() = %#v, %v", got, err)
	}
	if got, err := service.QueryConfirmation(tx, "task-1"); err != nil || !reflect.DeepEqual(got, confirmation) {
		t.Fatalf("QueryConfirmation() = %#v, %v", got, err)
	}
	if got, err := service.QueryEvaluator(tx, "eval-1"); err != nil || !reflect.DeepEqual(got, evaluator) {
		t.Fatalf("QueryEvaluator() = %#v, %v", got, err)
	}
	if got, err := service.QueryTaskReputation(tx, "model-1", "task-1"); err != nil || !reflect.DeepEqual(got, reputation) {
		t.Fatalf("QueryTaskReputation() = %#v, %v", got, err)
	}

	if _, err := service.QueryAllocation(tx, "missing"); err == nil {
		t.Fatal("QueryAllocation(missing) returned no error")
	}
	if _, err := service.QueryConfirmation(tx, "missing"); err == nil {
		t.Fatal("QueryConfirmation(missing) returned no error")
	}
	if _, err := service.QueryEvaluator(tx, "missing"); err == nil {
		t.Fatal("QueryEvaluator(missing) returned no error")
	}
	if _, err := service.QueryTaskReputation(tx, "model-1", "missing"); err == nil {
		t.Fatal("QueryTaskReputation(missing) returned no error")
	}
}

func readyAllocationTx(t *testing.T) *memoryTx {
	t.Helper()
	tx := newMemoryTx()
	task := core.Task{
		TaskID:           "task-1",
		SubjectID:        "model-1",
		MinEvaluators:    2,
		MinReputationPPM: 400_000,
	}
	if err := tx.Put(core.TaskKey(task.TaskID), task); err != nil {
		t.Fatal(err)
	}
	for _, evalID := range []string{"eval-1", "eval-2", "eval-3"} {
		state := core.EvaluatorState{
			EvalID:        evalID,
			ClientID:      evalID + "-client",
			MSPID:         "Org1MSP",
			ReputationPPM: 500_000,
		}
		if err := tx.Put(core.EvaluatorKey(evalID), state); err != nil {
			t.Fatal(err)
		}
	}
	tx.caller.Attributes["role"] = "audit_service"
	return tx
}

func validAllocationInput() core.PostAllocationInput {
	return core.PostAllocationInput{
		TaskID: "task-1",
		Members: []core.AllocationMember{
			{EvalID: "eval-2", SharePPM: 500_000},
			{EvalID: "eval-1", SharePPM: 500_000},
		},
	}
}

func TestPostAllocation(t *testing.T) {
	service := core.Service{}

	tests := []struct {
		name   string
		setup  func(*testing.T, *memoryTx)
		mutate func(*core.PostAllocationInput)
		code   string
	}{
		{
			name: "non-audit caller",
			setup: func(_ *testing.T, tx *memoryTx) {
				tx.caller.Attributes["role"] = "evaluator"
			},
			code: "ERR_UNAUTHORIZED",
		},
		{
			name: "missing task",
			mutate: func(input *core.PostAllocationInput) {
				input.TaskID = "missing"
			},
			code: "ERR_TASK_NOT_FOUND",
		},
		{
			name: "duplicate member",
			mutate: func(input *core.PostAllocationInput) {
				input.Members[1].EvalID = input.Members[0].EvalID
			},
			code: "ERR_DUPLICATE_MEMBER",
		},
		{
			name: "unknown evaluator",
			mutate: func(input *core.PostAllocationInput) {
				input.Members[1].EvalID = "unknown"
			},
			code: "ERR_EVALUATOR_NOT_FOUND",
		},
		{
			name: "reputation below threshold",
			setup: func(t *testing.T, tx *memoryTx) {
				var state core.EvaluatorState
				_, _ = tx.Get(core.EvaluatorKey("eval-1"), &state)
				state.ReputationPPM = 399_999
				if err := tx.Put(core.EvaluatorKey("eval-1"), state); err != nil {
					t.Fatal(err)
				}
			},
			code: "ERR_REPUTATION_THRESHOLD",
		},
		{
			name: "occupied evaluator",
			setup: func(t *testing.T, tx *memoryTx) {
				lock := core.ActiveTaskOccupancy{EvalID: "eval-1", TaskID: "other-task"}
				if err := tx.Put(core.OccupancyKey("eval-1"), lock); err != nil {
					t.Fatal(err)
				}
			},
			code: "ERR_MEMBER_LOCKED",
		},
		{
			name: "invalid share sum",
			mutate: func(input *core.PostAllocationInput) {
				input.Members[1].SharePPM = 499_999
			},
			code: "ERR_SHARE_SUM",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			tx := readyAllocationTx(t)
			input := validAllocationInput()
			if test.setup != nil {
				test.setup(t, tx)
			}
			if test.mutate != nil {
				test.mutate(&input)
			}
			before := tx.snapshot()
			requireCode(t, service.PostAllocation(tx, input), test.code)
			if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
				t.Fatalf("rejected allocation changed state")
			}
		})
	}

	tx := readyAllocationTx(t)
	input := validAllocationInput()
	if err := service.PostAllocation(tx, input); err != nil {
		t.Fatalf("PostAllocation() error = %v", err)
	}
	var allocation core.Allocation
	found, err := tx.Get(core.AllocationKey(input.TaskID), &allocation)
	if err != nil || !found {
		t.Fatalf("allocation lookup found=%v error=%v", found, err)
	}
	if allocation.Status != "Active" || allocation.Members[0].EvalID != "eval-1" {
		t.Fatalf("allocation not active and sorted: %#v", allocation)
	}
	for _, member := range allocation.Members {
		var lock core.ActiveTaskOccupancy
		found, err := tx.Get(core.OccupancyKey(member.EvalID), &lock)
		if err != nil || !found || lock.TaskID != input.TaskID {
			t.Fatalf("lock for %s found=%v error=%v value=%#v", member.EvalID, found, err, lock)
		}
	}

	before := tx.snapshot()
	requireCode(t, service.PostAllocation(tx, input), "ERR_ALLOCATION_EXISTS")
	if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
		t.Fatalf("duplicate allocation changed state")
	}
}

func TestConfirmationDigest(t *testing.T) {
	evals := []core.EvalItem{
		{EvalID: "eval-2", ScorePPM: 300_000, Verdict: "unsafe"},
		{EvalID: "eval-1", ScorePPM: 900_000, Verdict: "safe"},
	}
	refs := []core.EvidenceRef{
		{EvalID: "eval-2", TaskID: "task-1", CID: "cid-2", SHA256: strings.Repeat("2", 64), SubmitterClientID: "client-2", SubmitterMSPID: "Org2MSP"},
		{EvalID: "eval-1", TaskID: "task-1", CID: "cid-1", SHA256: strings.Repeat("1", 64), SubmitterClientID: "client-1", SubmitterMSPID: "Org1MSP"},
	}

	base, err := core.ConfirmationDigest("task-1", evals, refs, 1234)
	if err != nil {
		t.Fatalf("ConfirmationDigest() error = %v", err)
	}
	reordered, err := core.ConfirmationDigest(
		"task-1",
		[]core.EvalItem{evals[1], evals[0]},
		[]core.EvidenceRef{refs[1], refs[0]},
		1234,
	)
	if err != nil || reordered != base {
		t.Fatalf("reordered digest = %q, error=%v, want %q", reordered, err, base)
	}

	tests := []struct {
		name     string
		evals    []core.EvalItem
		refs     []core.EvidenceRef
		deadline int64
	}{
		{name: "score", evals: []core.EvalItem{{EvalID: "eval-2", ScorePPM: 300_001, Verdict: "unsafe"}, evals[1]}, refs: refs, deadline: 1234},
		{name: "CID", evals: evals, refs: []core.EvidenceRef{{EvalID: "eval-2", TaskID: "task-1", CID: "changed", SHA256: refs[0].SHA256, SubmitterClientID: "client-2", SubmitterMSPID: "Org2MSP"}, refs[1]}, deadline: 1234},
		{name: "hash", evals: evals, refs: []core.EvidenceRef{{EvalID: "eval-2", TaskID: "task-1", CID: "cid-2", SHA256: strings.Repeat("3", 64), SubmitterClientID: "client-2", SubmitterMSPID: "Org2MSP"}, refs[1]}, deadline: 1234},
		{name: "deadline", evals: evals, refs: refs, deadline: 1235},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := core.ConfirmationDigest("task-1", test.evals, test.refs, test.deadline)
			if err != nil {
				t.Fatal(err)
			}
			if got == base {
				t.Fatalf("changed input retained digest %s", got)
			}
		})
	}
}

func confirmationFixture(t *testing.T) (*memoryTx, core.SnapshotInput, core.Confirmation) {
	t.Helper()
	tx := newMemoryTx()
	tx.now = 1_000
	tx.caller.Attributes["role"] = "audit_service"
	task := core.Task{TaskID: "task-1", SubjectID: "model-1", DeadlineUnix: 2_000, MinEvaluators: 3}
	if err := tx.Put(core.TaskKey(task.TaskID), task); err != nil {
		t.Fatal(err)
	}
	members := []core.AllocationMember{
		{EvalID: "eval-1", SharePPM: 333_334},
		{EvalID: "eval-2", SharePPM: 333_333},
		{EvalID: "eval-3", SharePPM: 333_333},
	}
	for _, member := range members {
		evaluator := core.EvaluatorState{EvalID: member.EvalID, ClientID: member.EvalID + "-client", MSPID: "Org1MSP", AlphaMicro: core.OnePPM, BetaMicro: core.OnePPM, ReputationPPM: 500_000}
		if err := tx.Put(core.EvaluatorKey(member.EvalID), evaluator); err != nil {
			t.Fatal(err)
		}
	}
	allocation := core.Allocation{TaskID: task.TaskID, Members: members, Status: "Active", CreatedAtUnix: tx.now}
	if err := tx.Put(core.AllocationKey(task.TaskID), allocation); err != nil {
		t.Fatal(err)
	}
	input := core.SnapshotInput{TaskID: task.TaskID, DeadlineUnix: task.DeadlineUnix}
	for index, member := range members {
		input.EvalItems = append(input.EvalItems, core.EvalItem{EvalID: member.EvalID, ScorePPM: int64(600_000 + index), Verdict: "safe"})
		input.EvidenceRefs = append(input.EvidenceRefs, core.EvidenceRef{
			EvalID: member.EvalID, TaskID: task.TaskID, CID: "cid-" + member.EvalID,
			SHA256:            strings.Repeat(string(rune('a'+index)), 64),
			SubmitterClientID: member.EvalID + "-client", SubmitterMSPID: "Org1MSP",
		})
	}
	confirmation, err := (core.Service{}).PostEvalSnapshot(tx, input)
	if err != nil {
		t.Fatalf("PostEvalSnapshot() error = %v", err)
	}
	return tx, input, confirmation
}

func TestConfirmationSnapshot(t *testing.T) {
	service := core.Service{}

	for _, test := range []struct {
		name   string
		mutate func(*core.SnapshotInput)
	}{
		{name: "evaluation member mismatch", mutate: func(input *core.SnapshotInput) { input.EvalItems[0].EvalID = "other" }},
		{name: "evidence member mismatch", mutate: func(input *core.SnapshotInput) { input.EvidenceRefs[0].EvalID = "other" }},
	} {
		t.Run(test.name, func(t *testing.T) {
			tx, input, _ := confirmationFixture(t)
			delete(tx.state, core.ConfirmationKey(input.TaskID))
			test.mutate(&input)
			before := tx.snapshot()
			_, err := service.PostEvalSnapshot(tx, input)
			requireCode(t, err, "ERR_SNAPSHOT_MEMBERSHIP")
			if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
				t.Fatalf("rejected snapshot changed state")
			}
		})
	}

	tx, input, confirmation := confirmationFixture(t)
	if confirmation.Status != "Pending" || confirmation.Digest == "" {
		t.Fatalf("unexpected confirmation: %#v", confirmation)
	}
	before := tx.snapshot()
	input.EvalItems[0].ScorePPM--
	_, err := service.PostEvalSnapshot(tx, input)
	requireCode(t, err, "ERR_CONFIRMATION_EXISTS")
	if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
		t.Fatalf("snapshot mutation changed state")
	}
}

func TestVoteStateMachine(t *testing.T) {
	service := core.Service{}

	t.Run("identity digest and duplicate checks", func(t *testing.T) {
		tx, _, confirmation := confirmationFixture(t)
		tx.caller = core.Caller{ClientID: "outsider", MSPID: "Org1MSP", Attributes: map[string]string{"role": "evaluator"}}
		_, err := service.SubmitVote(tx, confirmation.TaskID, confirmation.Digest, "ACK")
		requireCode(t, err, "ERR_NOT_COMMITTEE_MEMBER")

		tx.caller.ClientID = "eval-1-client"
		_, err = service.SubmitVote(tx, confirmation.TaskID, strings.Repeat("f", 64), "ACK")
		requireCode(t, err, "ERR_DIGEST_MISMATCH")

		got, err := service.SubmitVote(tx, confirmation.TaskID, confirmation.Digest, "ACK")
		if err != nil || got.Status != "Pending" || got.AckCount != 1 {
			t.Fatalf("first vote confirmation=%#v error=%v", got, err)
		}
		before := tx.snapshot()
		_, err = service.SubmitVote(tx, confirmation.TaskID, confirmation.Digest, "OBJECT")
		requireCode(t, err, "ERR_DUPLICATE_VOTE")
		if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
			t.Fatalf("duplicate cross-vote changed state")
		}
	})

	t.Run("accept at two-thirds ceiling", func(t *testing.T) {
		tx, _, confirmation := confirmationFixture(t)
		for index, clientID := range []string{"eval-1-client", "eval-2-client"} {
			tx.caller.ClientID = clientID
			got, err := service.SubmitVote(tx, confirmation.TaskID, confirmation.Digest, "ACK")
			if err != nil {
				t.Fatal(err)
			}
			want := "Pending"
			if index == 1 {
				want = "Accept"
			}
			if got.Status != want {
				t.Fatalf("vote %d status=%s want=%s", index+1, got.Status, want)
			}
		}
	})

	t.Run("objection enters review", func(t *testing.T) {
		tx, _, confirmation := confirmationFixture(t)
		tx.caller.ClientID = "eval-3-client"
		got, err := service.SubmitVote(tx, confirmation.TaskID, confirmation.Digest, "OBJECT")
		if err != nil || got.Status != "Review" || got.ObjectCount != 1 {
			t.Fatalf("confirmation=%#v error=%v", got, err)
		}
	})

	t.Run("expired pending enters review", func(t *testing.T) {
		tx, _, confirmation := confirmationFixture(t)
		tx.now = confirmation.DeadlineUnix + 1
		got, err := service.FinalizeConfirmation(tx, confirmation.TaskID)
		if err != nil || got.Status != "Review" {
			t.Fatalf("confirmation=%#v error=%v", got, err)
		}
	})
}

func TestReviewDecision(t *testing.T) {
	service := core.Service{}
	tx, _, confirmation := confirmationFixture(t)
	tx.caller.ClientID = "eval-1-client"
	confirmation, err := service.SubmitVote(tx, confirmation.TaskID, confirmation.Digest, "OBJECT")
	if err != nil {
		t.Fatal(err)
	}
	tx.caller.Attributes["role"] = "audit_service"
	got, err := service.PostReviewDecision(tx, confirmation.TaskID, "Accept")
	if err != nil || got.Status != "Accept" {
		t.Fatalf("confirmation=%#v error=%v", got, err)
	}
}

func acceptedSettlementFixture(t *testing.T) (*memoryTx, core.Confirmation) {
	t.Helper()
	tx, _, confirmation := confirmationFixture(t)
	service := core.Service{}
	for _, clientID := range []string{"eval-1-client", "eval-2-client"} {
		tx.caller.ClientID = clientID
		var err error
		confirmation, err = service.SubmitVote(tx, confirmation.TaskID, confirmation.Digest, "ACK")
		if err != nil {
			t.Fatal(err)
		}
	}
	var allocation core.Allocation
	_, _ = tx.Get(core.AllocationKey(confirmation.TaskID), &allocation)
	for _, member := range allocation.Members {
		lock := core.ActiveTaskOccupancy{EvalID: member.EvalID, TaskID: confirmation.TaskID, LockedAtUnix: 900}
		if err := tx.Put(core.OccupancyKey(member.EvalID), lock); err != nil {
			t.Fatal(err)
		}
	}
	tx.caller.Attributes["role"] = "audit_service"
	tx.now = 1_500
	return tx, confirmation
}

func TestProcessSettlement(t *testing.T) {
	service := core.Service{}

	tests := []struct {
		name  string
		setup func(*testing.T, *memoryTx, core.Confirmation)
		code  string
	}{
		{name: "missing task", setup: func(_ *testing.T, tx *memoryTx, c core.Confirmation) { delete(tx.state, core.TaskKey(c.TaskID)) }, code: "ERR_TASK_NOT_FOUND"},
		{name: "missing allocation", setup: func(_ *testing.T, tx *memoryTx, c core.Confirmation) { delete(tx.state, core.AllocationKey(c.TaskID)) }, code: "ERR_ALLOCATION_NOT_FOUND"},
		{name: "missing confirmation", setup: func(_ *testing.T, tx *memoryTx, c core.Confirmation) {
			delete(tx.state, core.ConfirmationKey(c.TaskID))
		}, code: "ERR_CONFIRMATION_NOT_FOUND"},
		{name: "pending confirmation", setup: func(t *testing.T, tx *memoryTx, c core.Confirmation) {
			c.Status = "Pending"
			c.Consumed = false
			if err := tx.Put(core.ConfirmationKey(c.TaskID), c); err != nil {
				t.Fatal(err)
			}
		}, code: "ERR_CONFIRMATION_NOT_ACCEPTED"},
		{name: "review confirmation", setup: func(t *testing.T, tx *memoryTx, c core.Confirmation) {
			c.Status = "Review"
			c.Consumed = false
			if err := tx.Put(core.ConfirmationKey(c.TaskID), c); err != nil {
				t.Fatal(err)
			}
		}, code: "ERR_CONFIRMATION_NOT_ACCEPTED"},
		{name: "rejected confirmation", setup: func(t *testing.T, tx *memoryTx, c core.Confirmation) {
			c.Status = "Reject"
			c.Consumed = false
			if err := tx.Put(core.ConfirmationKey(c.TaskID), c); err != nil {
				t.Fatal(err)
			}
		}, code: "ERR_CONFIRMATION_NOT_ACCEPTED"},
		{name: "consumed confirmation", setup: func(t *testing.T, tx *memoryTx, c core.Confirmation) {
			c.Consumed = true
			if err := tx.Put(core.ConfirmationKey(c.TaskID), c); err != nil {
				t.Fatal(err)
			}
		}, code: "ERR_ALREADY_SETTLED"},
		{name: "membership mismatch", setup: func(t *testing.T, tx *memoryTx, c core.Confirmation) {
			c.EvalItems[0].EvalID = "other"
			if err := tx.Put(core.ConfirmationKey(c.TaskID), c); err != nil {
				t.Fatal(err)
			}
		}, code: "ERR_SNAPSHOT_MEMBERSHIP"},
		{name: "invalid score", setup: func(t *testing.T, tx *memoryTx, c core.Confirmation) {
			c.EvalItems[0].ScorePPM = core.OnePPM + 1
			if err := tx.Put(core.ConfirmationKey(c.TaskID), c); err != nil {
				t.Fatal(err)
			}
		}, code: "ERR_INVALID_SCORE"},
		{name: "missing evaluator", setup: func(_ *testing.T, tx *memoryTx, _ core.Confirmation) { delete(tx.state, core.EvaluatorKey("eval-1")) }, code: "ERR_EVALUATOR_NOT_FOUND"},
		{name: "missing lock", setup: func(_ *testing.T, tx *memoryTx, _ core.Confirmation) { delete(tx.state, core.OccupancyKey("eval-1")) }, code: "ERR_MEMBER_LOCKED"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			tx, confirmation := acceptedSettlementFixture(t)
			test.setup(t, tx, confirmation)
			before := tx.snapshot()
			_, err := service.ProcessSettlement(tx, confirmation.TaskID)
			requireCode(t, err, test.code)
			if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
				t.Fatalf("rejected settlement changed state")
			}
		})
	}

	tx, confirmation := acceptedSettlementFixture(t)
	result, err := service.ProcessSettlement(tx, confirmation.TaskID)
	if err != nil {
		t.Fatalf("ProcessSettlement() error = %v", err)
	}
	var allocation core.Allocation
	_, _ = tx.Get(core.AllocationKey(confirmation.TaskID), &allocation)
	if allocation.Status != "Settled" || allocation.SettledAtUnix != tx.now {
		t.Fatalf("allocation not settled: %#v", allocation)
	}
	var storedConfirmation core.Confirmation
	_, _ = tx.Get(core.ConfirmationKey(confirmation.TaskID), &storedConfirmation)
	if !storedConfirmation.Consumed {
		t.Fatalf("confirmation not consumed")
	}

	var weighted int64
	for index, member := range allocation.Members {
		var evaluator core.EvaluatorState
		found, getErr := tx.Get(core.EvaluatorKey(member.EvalID), &evaluator)
		if getErr != nil || !found {
			t.Fatalf("evaluator %s missing: %v", member.EvalID, getErr)
		}
		score := int64(600_000 + index)
		wantAlpha := core.OnePPM + score
		wantBeta := core.OnePPM + core.OnePPM - score
		wantReputation := wantAlpha * core.OnePPM / (wantAlpha + wantBeta)
		if evaluator.AlphaMicro != wantAlpha || evaluator.BetaMicro != wantBeta || evaluator.ReputationPPM != wantReputation {
			t.Fatalf("evaluator %s state=%#v", member.EvalID, evaluator)
		}
		weighted += member.SharePPM * wantReputation
		var lock core.ActiveTaskOccupancy
		if found, _ := tx.Get(core.OccupancyKey(member.EvalID), &lock); found {
			t.Fatalf("lock for %s was not released", member.EvalID)
		}
	}
	if result.ReputationPPM != weighted/core.OnePPM {
		t.Fatalf("task reputation=%d want=%d", result.ReputationPPM, weighted/core.OnePPM)
	}

	before := tx.snapshot()
	_, err = service.ProcessSettlement(tx, confirmation.TaskID)
	requireCode(t, err, "ERR_ALREADY_SETTLED")
	if after := tx.snapshot(); !reflect.DeepEqual(after, before) {
		t.Fatalf("replayed settlement changed state")
	}
}
