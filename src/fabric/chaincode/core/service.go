package core

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
)

type Service struct{}

type RegisterEvaluatorInput struct {
	EvalID       string   `json:"evalId"`
	Capabilities []string `json:"capabilities"`
}

type PostTaskInput struct {
	TaskID           string   `json:"taskId"`
	SubjectID        string   `json:"subjectId"`
	RiskCategories   []string `json:"riskCategories"`
	Modalities       []string `json:"modalities"`
	Workload         int64    `json:"workload"`
	DeadlineUnix     int64    `json:"deadlineUnix"`
	InputBytes       int64    `json:"inputBytes"`
	Priority         int64    `json:"priority"`
	MinEvaluators    int64    `json:"minEvaluators"`
	MinReputationPPM int64    `json:"minReputationPpm"`
	CID              string   `json:"cid"`
	SHA256           string   `json:"sha256"`
	InlinePayload    string   `json:"inlinePayload,omitempty"`
}

type PostAllocationInput struct {
	TaskID  string             `json:"taskId"`
	Members []AllocationMember `json:"members"`
}

type SnapshotInput struct {
	TaskID       string        `json:"taskId"`
	EvalItems    []EvalItem    `json:"evalItems"`
	EvidenceRefs []EvidenceRef `json:"evidenceRefs"`
	DeadlineUnix int64         `json:"deadlineUnix"`
}

func (Service) RegisterEvaluator(tx Tx, input RegisterEvaluatorInput) error {
	if strings.TrimSpace(input.EvalID) == "" {
		return CodeError{Code: "ERR_INVALID_EVALUATOR", Message: "evaluator ID is required"}
	}

	key := EvaluatorKey(input.EvalID)
	var existing EvaluatorState
	found, err := tx.Get(key, &existing)
	if err != nil {
		return fmt.Errorf("read evaluator: %w", err)
	}
	if found {
		return CodeError{Code: "ERR_EVALUATOR_EXISTS", Message: input.EvalID}
	}

	caller := tx.Caller()
	state := EvaluatorState{
		EvalID:         input.EvalID,
		ClientID:       caller.ClientID,
		MSPID:          caller.MSPID,
		Capabilities:   append([]string(nil), input.Capabilities...),
		AlphaMicro:     OnePPM,
		BetaMicro:      OnePPM,
		ReputationPPM:  OnePPM / 2,
		RegisteredUnix: tx.TimestampUnix(),
	}
	if err := tx.Put(key, state); err != nil {
		return fmt.Errorf("write evaluator: %w", err)
	}
	return nil
}

func (Service) PostTaskConstraint(tx Tx, input PostTaskInput) error {
	if strings.TrimSpace(input.TaskID) == "" {
		return CodeError{Code: "ERR_INVALID_TASK", Message: "task ID is required"}
	}
	if strings.TrimSpace(input.CID) == "" {
		return CodeError{Code: "ERR_INVALID_CID", Message: "CID is required"}
	}
	if !isSHA256(input.SHA256) {
		return CodeError{Code: "ERR_INVALID_HASH", Message: "expected a hexadecimal SHA-256 digest"}
	}
	if input.InlinePayload != "" {
		if input.InputBytes != int64(len([]byte(input.InlinePayload))) {
			return CodeError{Code: "ERR_INPUT_LENGTH_MISMATCH", Message: input.TaskID}
		}
		digest := sha256.Sum256([]byte(input.InlinePayload))
		if hex.EncodeToString(digest[:]) != strings.ToLower(input.SHA256) {
			return CodeError{Code: "ERR_EVIDENCE_HASH_MISMATCH", Message: input.TaskID}
		}
	}
	if input.MinEvaluators < 1 {
		return CodeError{Code: "ERR_INVALID_MIN_EVALUATORS", Message: "minimum evaluator count must be positive"}
	}
	if input.MinReputationPPM < 0 || input.MinReputationPPM > OnePPM {
		return CodeError{Code: "ERR_INVALID_REPUTATION", Message: "reputation threshold is outside the PPM range"}
	}

	key := TaskKey(input.TaskID)
	var existing Task
	found, err := tx.Get(key, &existing)
	if err != nil {
		return fmt.Errorf("read task: %w", err)
	}
	if found {
		return CodeError{Code: "ERR_TASK_EXISTS", Message: input.TaskID}
	}

	caller := tx.Caller()
	task := Task{
		TaskID:           input.TaskID,
		SubjectID:        input.SubjectID,
		RiskCategories:   append([]string(nil), input.RiskCategories...),
		Modalities:       append([]string(nil), input.Modalities...),
		Workload:         input.Workload,
		DeadlineUnix:     input.DeadlineUnix,
		InputBytes:       input.InputBytes,
		Priority:         input.Priority,
		MinEvaluators:    input.MinEvaluators,
		MinReputationPPM: input.MinReputationPPM,
		CID:              input.CID,
		SHA256:           strings.ToLower(input.SHA256),
		InlinePayload:    input.InlinePayload,
		CreatorClientID:  caller.ClientID,
		CreatorMSPID:     caller.MSPID,
		CreatedAtUnix:    tx.TimestampUnix(),
	}
	if err := tx.Put(key, task); err != nil {
		return fmt.Errorf("write task: %w", err)
	}
	return nil
}

func (Service) QueryTask(tx Tx, taskID string) (Task, error) {
	var task Task
	found, err := tx.Get(TaskKey(taskID), &task)
	if err != nil {
		return Task{}, fmt.Errorf("read task: %w", err)
	}
	if !found {
		return Task{}, CodeError{Code: "ERR_TASK_NOT_FOUND", Message: taskID}
	}
	return task, nil
}

func (Service) QueryAllocation(tx Tx, taskID string) (Allocation, error) {
	var allocation Allocation
	found, err := tx.Get(AllocationKey(taskID), &allocation)
	if err != nil {
		return Allocation{}, fmt.Errorf("read allocation: %w", err)
	}
	if !found {
		return Allocation{}, CodeError{Code: "ERR_ALLOCATION_NOT_FOUND", Message: taskID}
	}
	return allocation, nil
}

func (Service) QueryConfirmation(tx Tx, taskID string) (Confirmation, error) {
	var confirmation Confirmation
	found, err := tx.Get(ConfirmationKey(taskID), &confirmation)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read confirmation: %w", err)
	}
	if !found {
		return Confirmation{}, CodeError{Code: "ERR_CONFIRMATION_NOT_FOUND", Message: taskID}
	}
	return confirmation, nil
}

func (Service) QueryEvaluator(tx Tx, evalID string) (EvaluatorState, error) {
	var evaluator EvaluatorState
	found, err := tx.Get(EvaluatorKey(evalID), &evaluator)
	if err != nil {
		return EvaluatorState{}, fmt.Errorf("read evaluator: %w", err)
	}
	if !found {
		return EvaluatorState{}, CodeError{Code: "ERR_EVALUATOR_NOT_FOUND", Message: evalID}
	}
	return evaluator, nil
}

func (Service) QueryTaskReputation(tx Tx, subjectID, taskID string) (TaskReputation, error) {
	var reputation TaskReputation
	found, err := tx.Get(TaskReputationKey(subjectID, taskID), &reputation)
	if err != nil {
		return TaskReputation{}, fmt.Errorf("read task reputation: %w", err)
	}
	if !found {
		return TaskReputation{}, CodeError{Code: "ERR_TASK_REPUTATION_NOT_FOUND", Message: taskID}
	}
	return reputation, nil
}

func (Service) PostAllocation(tx Tx, input PostAllocationInput) error {
	if tx.Caller().Attributes["role"] != "audit_service" {
		return CodeError{Code: "ERR_UNAUTHORIZED", Message: "audit_service role is required"}
	}

	var task Task
	found, err := tx.Get(TaskKey(input.TaskID), &task)
	if err != nil {
		return fmt.Errorf("read task: %w", err)
	}
	if !found {
		return CodeError{Code: "ERR_TASK_NOT_FOUND", Message: input.TaskID}
	}

	var existing Allocation
	found, err = tx.Get(AllocationKey(input.TaskID), &existing)
	if err != nil {
		return fmt.Errorf("read allocation: %w", err)
	}
	if found {
		return CodeError{Code: "ERR_ALLOCATION_EXISTS", Message: input.TaskID}
	}
	if int64(len(input.Members)) < task.MinEvaluators {
		return CodeError{Code: "ERR_MEMBER_COUNT", Message: "committee is smaller than the task minimum"}
	}
	if SumShares(input.Members) != OnePPM {
		return CodeError{Code: "ERR_SHARE_SUM", Message: "allocation shares must sum to one million PPM"}
	}

	seen := make(map[string]struct{}, len(input.Members))
	for _, member := range input.Members {
		if _, duplicate := seen[member.EvalID]; duplicate {
			return CodeError{Code: "ERR_DUPLICATE_MEMBER", Message: member.EvalID}
		}
		seen[member.EvalID] = struct{}{}

		var evaluator EvaluatorState
		found, err = tx.Get(EvaluatorKey(member.EvalID), &evaluator)
		if err != nil {
			return fmt.Errorf("read evaluator %s: %w", member.EvalID, err)
		}
		if !found {
			return CodeError{Code: "ERR_EVALUATOR_NOT_FOUND", Message: member.EvalID}
		}
		if evaluator.ReputationPPM < task.MinReputationPPM {
			return CodeError{Code: "ERR_REPUTATION_THRESHOLD", Message: member.EvalID}
		}

		var lock ActiveTaskOccupancy
		found, err = tx.Get(OccupancyKey(member.EvalID), &lock)
		if err != nil {
			return fmt.Errorf("read occupancy %s: %w", member.EvalID, err)
		}
		if found {
			return CodeError{Code: "ERR_MEMBER_LOCKED", Message: member.EvalID}
		}
	}

	members := append([]AllocationMember(nil), input.Members...)
	sort.Slice(members, func(i, j int) bool { return members[i].EvalID < members[j].EvalID })
	now := tx.TimestampUnix()
	allocation := Allocation{
		TaskID:        input.TaskID,
		Members:       members,
		Status:        "Active",
		CreatedAtUnix: now,
	}
	if err := tx.Put(AllocationKey(input.TaskID), allocation); err != nil {
		return fmt.Errorf("write allocation: %w", err)
	}
	for _, member := range members {
		lock := ActiveTaskOccupancy{EvalID: member.EvalID, TaskID: input.TaskID, LockedAtUnix: now}
		if err := tx.Put(OccupancyKey(member.EvalID), lock); err != nil {
			return fmt.Errorf("write occupancy %s: %w", member.EvalID, err)
		}
	}
	return nil
}

func (Service) PostEvalSnapshot(tx Tx, input SnapshotInput) (Confirmation, error) {
	if tx.Caller().Attributes["role"] != "audit_service" {
		return Confirmation{}, CodeError{Code: "ERR_UNAUTHORIZED", Message: "audit_service role is required"}
	}

	var task Task
	found, err := tx.Get(TaskKey(input.TaskID), &task)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read task: %w", err)
	}
	if !found {
		return Confirmation{}, CodeError{Code: "ERR_TASK_NOT_FOUND", Message: input.TaskID}
	}
	if input.DeadlineUnix != task.DeadlineUnix {
		return Confirmation{}, CodeError{Code: "ERR_DEADLINE_MISMATCH", Message: input.TaskID}
	}

	var allocation Allocation
	found, err = tx.Get(AllocationKey(input.TaskID), &allocation)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read allocation: %w", err)
	}
	if !found || allocation.Status != "Active" {
		return Confirmation{}, CodeError{Code: "ERR_ALLOCATION_NOT_ACTIVE", Message: input.TaskID}
	}

	var existing Confirmation
	found, err = tx.Get(ConfirmationKey(input.TaskID), &existing)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read confirmation: %w", err)
	}
	if found {
		return Confirmation{}, CodeError{Code: "ERR_CONFIRMATION_EXISTS", Message: input.TaskID}
	}

	if err := validateSnapshot(tx, allocation, input); err != nil {
		return Confirmation{}, err
	}
	evals := append([]EvalItem(nil), input.EvalItems...)
	refs := append([]EvidenceRef(nil), input.EvidenceRefs...)
	sort.Slice(evals, func(i, j int) bool { return evals[i].EvalID < evals[j].EvalID })
	sort.Slice(refs, func(i, j int) bool { return refs[i].EvalID < refs[j].EvalID })
	digest, err := ConfirmationDigest(input.TaskID, evals, refs, input.DeadlineUnix)
	if err != nil {
		return Confirmation{}, err
	}
	confirmation := Confirmation{
		TaskID:        input.TaskID,
		EvalItems:     evals,
		EvidenceRefs:  refs,
		Digest:        digest,
		DeadlineUnix:  input.DeadlineUnix,
		Status:        "Pending",
		CreatedAtUnix: tx.TimestampUnix(),
	}
	if err := tx.Put(ConfirmationKey(input.TaskID), confirmation); err != nil {
		return Confirmation{}, fmt.Errorf("write confirmation: %w", err)
	}
	return confirmation, nil
}

func (Service) SubmitVote(tx Tx, taskID, digest, voteType string) (Confirmation, error) {
	var confirmation Confirmation
	found, err := tx.Get(ConfirmationKey(taskID), &confirmation)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read confirmation: %w", err)
	}
	if !found {
		return Confirmation{}, CodeError{Code: "ERR_CONFIRMATION_NOT_FOUND", Message: taskID}
	}
	if confirmation.Status != "Pending" {
		return Confirmation{}, CodeError{Code: "ERR_CONFIRMATION_FINAL", Message: confirmation.Status}
	}
	if tx.TimestampUnix() > confirmation.DeadlineUnix {
		return Confirmation{}, CodeError{Code: "ERR_VOTE_CLOSED", Message: taskID}
	}
	if digest != confirmation.Digest {
		return Confirmation{}, CodeError{Code: "ERR_DIGEST_MISMATCH", Message: taskID}
	}
	voteType = strings.ToUpper(voteType)
	if voteType != "ACK" && voteType != "OBJECT" {
		return Confirmation{}, CodeError{Code: "ERR_INVALID_VOTE", Message: voteType}
	}

	var allocation Allocation
	found, err = tx.Get(AllocationKey(taskID), &allocation)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read allocation: %w", err)
	}
	if !found {
		return Confirmation{}, CodeError{Code: "ERR_ALLOCATION_NOT_FOUND", Message: taskID}
	}
	caller := tx.Caller()
	if _, err := evaluatorForCaller(tx, allocation, caller); err != nil {
		return Confirmation{}, err
	}

	voteKey := VoteKey(taskID, caller.ClientID)
	var existing Vote
	found, err = tx.Get(voteKey, &existing)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read vote: %w", err)
	}
	if found {
		return Confirmation{}, CodeError{Code: "ERR_DUPLICATE_VOTE", Message: caller.ClientID}
	}

	vote := Vote{TaskID: taskID, ClientID: caller.ClientID, ConfirmationDigest: digest, VoteType: voteType, CreatedAtUnix: tx.TimestampUnix()}
	if err := tx.Put(voteKey, vote); err != nil {
		return Confirmation{}, fmt.Errorf("write vote: %w", err)
	}
	if voteType == "OBJECT" {
		confirmation.ObjectCount++
		confirmation.Status = "Review"
		confirmation.FinalizedAtUnix = tx.TimestampUnix()
	} else {
		confirmation.AckCount++
		quorum := (2*int64(len(allocation.Members)) + 2) / 3
		if confirmation.AckCount >= quorum {
			confirmation.Status = "Accept"
			confirmation.FinalizedAtUnix = tx.TimestampUnix()
		}
	}
	if err := tx.Put(ConfirmationKey(taskID), confirmation); err != nil {
		return Confirmation{}, fmt.Errorf("update confirmation: %w", err)
	}
	return confirmation, nil
}

func (Service) FinalizeConfirmation(tx Tx, taskID string) (Confirmation, error) {
	var confirmation Confirmation
	found, err := tx.Get(ConfirmationKey(taskID), &confirmation)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read confirmation: %w", err)
	}
	if !found {
		return Confirmation{}, CodeError{Code: "ERR_CONFIRMATION_NOT_FOUND", Message: taskID}
	}
	if confirmation.Status != "Pending" {
		return confirmation, nil
	}
	if tx.TimestampUnix() <= confirmation.DeadlineUnix {
		return Confirmation{}, CodeError{Code: "ERR_DEADLINE_NOT_REACHED", Message: taskID}
	}
	confirmation.Status = "Review"
	confirmation.FinalizedAtUnix = tx.TimestampUnix()
	if err := tx.Put(ConfirmationKey(taskID), confirmation); err != nil {
		return Confirmation{}, fmt.Errorf("update confirmation: %w", err)
	}
	return confirmation, nil
}

func (Service) PostReviewDecision(tx Tx, taskID, decision string) (Confirmation, error) {
	if tx.Caller().Attributes["role"] != "audit_service" {
		return Confirmation{}, CodeError{Code: "ERR_UNAUTHORIZED", Message: "audit_service role is required"}
	}
	var confirmation Confirmation
	found, err := tx.Get(ConfirmationKey(taskID), &confirmation)
	if err != nil {
		return Confirmation{}, fmt.Errorf("read confirmation: %w", err)
	}
	if !found {
		return Confirmation{}, CodeError{Code: "ERR_CONFIRMATION_NOT_FOUND", Message: taskID}
	}
	if confirmation.Status != "Review" {
		return Confirmation{}, CodeError{Code: "ERR_NOT_IN_REVIEW", Message: confirmation.Status}
	}
	if decision != "Accept" && decision != "Reject" {
		return Confirmation{}, CodeError{Code: "ERR_INVALID_DECISION", Message: decision}
	}
	confirmation.Status = decision
	confirmation.FinalizedAtUnix = tx.TimestampUnix()
	if err := tx.Put(ConfirmationKey(taskID), confirmation); err != nil {
		return Confirmation{}, fmt.Errorf("update confirmation: %w", err)
	}
	// Release evaluator locks on Reject (same as ProcessSettlement does on Settle)
	if decision == "Reject" {
		var allocation Allocation
		found, err := tx.Get(AllocationKey(taskID), &allocation)
		if err != nil {
			return Confirmation{}, fmt.Errorf("read allocation: %w", err)
		}
		if found {
			for _, member := range allocation.Members {
				if err := tx.Delete(OccupancyKey(member.EvalID)); err != nil {
					return Confirmation{}, fmt.Errorf("release occupancy %s: %w", member.EvalID, err)
				}
			}
			allocation.Status = "Rejected"
			allocation.SettledAtUnix = tx.TimestampUnix()
			if err := tx.Put(AllocationKey(taskID), allocation); err != nil {
				return Confirmation{}, fmt.Errorf("update allocation: %w", err)
			}
		}
	}
	return confirmation, nil
}

func (Service) ProcessSettlement(tx Tx, taskID string) (TaskReputation, error) {
	if tx.Caller().Attributes["role"] != "audit_service" {
		return TaskReputation{}, CodeError{Code: "ERR_UNAUTHORIZED", Message: "audit_service role is required"}
	}

	var task Task
	found, err := tx.Get(TaskKey(taskID), &task)
	if err != nil {
		return TaskReputation{}, fmt.Errorf("read task: %w", err)
	}
	if !found {
		return TaskReputation{}, CodeError{Code: "ERR_TASK_NOT_FOUND", Message: taskID}
	}

	var allocation Allocation
	found, err = tx.Get(AllocationKey(taskID), &allocation)
	if err != nil {
		return TaskReputation{}, fmt.Errorf("read allocation: %w", err)
	}
	if !found {
		return TaskReputation{}, CodeError{Code: "ERR_ALLOCATION_NOT_FOUND", Message: taskID}
	}

	var confirmation Confirmation
	found, err = tx.Get(ConfirmationKey(taskID), &confirmation)
	if err != nil {
		return TaskReputation{}, fmt.Errorf("read confirmation: %w", err)
	}
	if !found {
		return TaskReputation{}, CodeError{Code: "ERR_CONFIRMATION_NOT_FOUND", Message: taskID}
	}
	if confirmation.Consumed || allocation.Status == "Settled" {
		return TaskReputation{}, CodeError{Code: "ERR_ALREADY_SETTLED", Message: taskID}
	}
	if confirmation.Status != "Accept" {
		return TaskReputation{}, CodeError{Code: "ERR_CONFIRMATION_NOT_ACCEPTED", Message: confirmation.Status}
	}
	if allocation.Status != "Active" {
		return TaskReputation{}, CodeError{Code: "ERR_ALLOCATION_NOT_ACTIVE", Message: allocation.Status}
	}

	scores := make(map[string]int64, len(confirmation.EvalItems))
	for _, item := range confirmation.EvalItems {
		if _, duplicate := scores[item.EvalID]; duplicate {
			return TaskReputation{}, CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: item.EvalID}
		}
		if item.ScorePPM < 0 || item.ScorePPM > OnePPM {
			return TaskReputation{}, CodeError{Code: "ERR_INVALID_SCORE", Message: item.EvalID}
		}
		scores[item.EvalID] = item.ScorePPM
	}
	if len(scores) != len(allocation.Members) {
		return TaskReputation{}, CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: taskID}
	}

	type evaluatorUpdate struct {
		member AllocationMember
		state  EvaluatorState
	}
	updates := make([]evaluatorUpdate, 0, len(allocation.Members))
	var weightedReputation int64
	for _, member := range allocation.Members {
		score, ok := scores[member.EvalID]
		if !ok {
			return TaskReputation{}, CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: member.EvalID}
		}
		var evaluator EvaluatorState
		found, err = tx.Get(EvaluatorKey(member.EvalID), &evaluator)
		if err != nil {
			return TaskReputation{}, fmt.Errorf("read evaluator %s: %w", member.EvalID, err)
		}
		if !found {
			return TaskReputation{}, CodeError{Code: "ERR_EVALUATOR_NOT_FOUND", Message: member.EvalID}
		}
		var lock ActiveTaskOccupancy
		found, err = tx.Get(OccupancyKey(member.EvalID), &lock)
		if err != nil {
			return TaskReputation{}, fmt.Errorf("read occupancy %s: %w", member.EvalID, err)
		}
		if !found || lock.TaskID != taskID {
			return TaskReputation{}, CodeError{Code: "ERR_MEMBER_LOCKED", Message: member.EvalID}
		}

		evaluator.AlphaMicro += score
		evaluator.BetaMicro += OnePPM - score
		total := evaluator.AlphaMicro + evaluator.BetaMicro
		if total <= 0 {
			return TaskReputation{}, CodeError{Code: "ERR_INVALID_REPUTATION_STATE", Message: member.EvalID}
		}
		evaluator.ReputationPPM = evaluator.AlphaMicro * OnePPM / total
		weightedReputation += member.SharePPM * evaluator.ReputationPPM
		updates = append(updates, evaluatorUpdate{member: member, state: evaluator})
	}

	now := tx.TimestampUnix()
	result := TaskReputation{
		SubjectID:     task.SubjectID,
		TaskID:        taskID,
		ReputationPPM: weightedReputation / OnePPM,
		UpdatedAtUnix: now,
	}
	for _, update := range updates {
		if err := tx.Put(EvaluatorKey(update.member.EvalID), update.state); err != nil {
			return TaskReputation{}, fmt.Errorf("update evaluator %s: %w", update.member.EvalID, err)
		}
		if err := tx.Delete(OccupancyKey(update.member.EvalID)); err != nil {
			return TaskReputation{}, fmt.Errorf("release occupancy %s: %w", update.member.EvalID, err)
		}
	}
	allocation.Status = "Settled"
	allocation.SettledAtUnix = now
	confirmation.Consumed = true
	if err := tx.Put(AllocationKey(taskID), allocation); err != nil {
		return TaskReputation{}, fmt.Errorf("update allocation: %w", err)
	}
	if err := tx.Put(ConfirmationKey(taskID), confirmation); err != nil {
		return TaskReputation{}, fmt.Errorf("consume confirmation: %w", err)
	}
	if err := tx.Put(TaskReputationKey(task.SubjectID, taskID), result); err != nil {
		return TaskReputation{}, fmt.Errorf("write task reputation: %w", err)
	}
	return result, nil
}

func validateSnapshot(tx Tx, allocation Allocation, input SnapshotInput) error {
	if len(input.EvalItems) != len(allocation.Members) || len(input.EvidenceRefs) != len(allocation.Members) {
		return CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: "snapshot size differs from allocation"}
	}
	expected := make(map[string]struct{}, len(allocation.Members))
	for _, member := range allocation.Members {
		expected[member.EvalID] = struct{}{}
	}
	seenEval := make(map[string]struct{}, len(input.EvalItems))
	for _, item := range input.EvalItems {
		if _, ok := expected[item.EvalID]; !ok {
			return CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: item.EvalID}
		}
		if _, duplicate := seenEval[item.EvalID]; duplicate {
			return CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: item.EvalID}
		}
		if item.ScorePPM < 0 || item.ScorePPM > OnePPM {
			return CodeError{Code: "ERR_INVALID_SCORE", Message: item.EvalID}
		}
		seenEval[item.EvalID] = struct{}{}
	}
	seenRef := make(map[string]struct{}, len(input.EvidenceRefs))
	for _, ref := range input.EvidenceRefs {
		if _, ok := expected[ref.EvalID]; !ok || ref.TaskID != input.TaskID {
			return CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: ref.EvalID}
		}
		if _, duplicate := seenRef[ref.EvalID]; duplicate {
			return CodeError{Code: "ERR_SNAPSHOT_MEMBERSHIP", Message: ref.EvalID}
		}
		if strings.TrimSpace(ref.CID) == "" || !isSHA256(ref.SHA256) {
			return CodeError{Code: "ERR_INVALID_EVIDENCE", Message: ref.EvalID}
		}
		var evaluator EvaluatorState
		found, err := tx.Get(EvaluatorKey(ref.EvalID), &evaluator)
		if err != nil {
			return fmt.Errorf("read evaluator %s: %w", ref.EvalID, err)
		}
		if !found || evaluator.ClientID != ref.SubmitterClientID || evaluator.MSPID != ref.SubmitterMSPID {
			return CodeError{Code: "ERR_EVIDENCE_IDENTITY", Message: ref.EvalID}
		}
		seenRef[ref.EvalID] = struct{}{}
	}
	return nil
}

func evaluatorForCaller(tx Tx, allocation Allocation, caller Caller) (EvaluatorState, error) {
	for _, member := range allocation.Members {
		var evaluator EvaluatorState
		found, err := tx.Get(EvaluatorKey(member.EvalID), &evaluator)
		if err != nil {
			return EvaluatorState{}, fmt.Errorf("read evaluator %s: %w", member.EvalID, err)
		}
		if found && evaluator.ClientID == caller.ClientID && evaluator.MSPID == caller.MSPID {
			return evaluator, nil
		}
	}
	return EvaluatorState{}, CodeError{Code: "ERR_NOT_COMMITTEE_MEMBER", Message: caller.ClientID}
}

func isSHA256(value string) bool {
	if len(value) != 64 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}
