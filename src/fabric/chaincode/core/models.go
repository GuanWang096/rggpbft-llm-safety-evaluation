package core

const OnePPM int64 = 1_000_000

type Task struct {
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
	CreatorClientID  string   `json:"creatorClientId"`
	CreatorMSPID     string   `json:"creatorMspId"`
	CreatedAtUnix    int64    `json:"createdAtUnix"`
}

type EvaluatorState struct {
	EvalID         string   `json:"evalId"`
	ClientID       string   `json:"clientId"`
	MSPID          string   `json:"mspId"`
	Capabilities   []string `json:"capabilities"`
	AlphaMicro     int64    `json:"alphaMicro"`
	BetaMicro      int64    `json:"betaMicro"`
	ReputationPPM  int64    `json:"reputationPpm"`
	RegisteredUnix int64    `json:"registeredUnix"`
}

type AllocationMember struct {
	EvalID   string `json:"evalId"`
	SharePPM int64  `json:"sharePpm"`
}

func SumShares(members []AllocationMember) int64 {
	var total int64
	for _, member := range members {
		total += member.SharePPM
	}
	return total
}

type Allocation struct {
	TaskID        string             `json:"taskId"`
	Members       []AllocationMember `json:"members"`
	Status        string             `json:"status"`
	CreatedAtUnix int64              `json:"createdAtUnix"`
	SettledAtUnix int64              `json:"settledAtUnix,omitempty"`
}

type ActiveTaskOccupancy struct {
	EvalID       string `json:"evalId"`
	TaskID       string `json:"taskId"`
	LockedAtUnix int64  `json:"lockedAtUnix"`
}

type EvalItem struct {
	EvalID   string `json:"evalId"`
	ScorePPM int64  `json:"scorePpm"`
	Verdict  string `json:"verdict"`
}

type EvidenceRef struct {
	EvalID            string `json:"evalId"`
	TaskID            string `json:"taskId"`
	CID               string `json:"cid"`
	SHA256            string `json:"sha256"`
	SubmitterClientID string `json:"submitterClientId"`
	SubmitterMSPID    string `json:"submitterMspId"`
}

type Vote struct {
	TaskID             string `json:"taskId"`
	ClientID           string `json:"clientId"`
	ConfirmationDigest string `json:"confirmationDigest"`
	VoteType           string `json:"voteType"`
	CreatedAtUnix      int64  `json:"createdAtUnix"`
}

type Confirmation struct {
	TaskID          string        `json:"taskId"`
	EvalItems       []EvalItem    `json:"evalItems"`
	EvidenceRefs    []EvidenceRef `json:"evidenceRefs"`
	Digest          string        `json:"digest"`
	DeadlineUnix    int64         `json:"deadlineUnix"`
	AckCount        int64         `json:"ackCount"`
	ObjectCount     int64         `json:"objectCount"`
	Status          string        `json:"status"`
	Consumed        bool          `json:"consumed"`
	CreatedAtUnix   int64         `json:"createdAtUnix"`
	FinalizedAtUnix int64         `json:"finalizedAtUnix,omitempty"`
}

type TaskReputation struct {
	SubjectID     string `json:"subjectId"`
	TaskID        string `json:"taskId"`
	ReputationPPM int64  `json:"reputationPpm"`
	UpdatedAtUnix int64  `json:"updatedAtUnix"`
}
