package core

import "encoding/json"

const (
	DecisionStatusSnapshotFrozen     = "SnapshotFrozen"
	DecisionStatusCommitteeConfirmed = "CommitteeConfirmed"
	DecisionStatusReview             = "Review"
	DecisionStatusCertified          = "Certified"
	DecisionStatusSettled            = "Settled"
)

type JudgeState struct {
	JudgeID              string `json:"judgeId"`
	Organization         string `json:"organization"`
	ModelID              string `json:"modelId"`
	ModelRevision        string `json:"modelRevision"`
	PolicySHA256         string `json:"policySha256"`
	AdapterVersion       string `json:"adapterVersion"`
	PublicKeyHex         string `json:"publicKeyHex"`
	PublicKeyFingerprint string `json:"publicKeyFingerprint"`
	UnsafeCorrectMicro   int64  `json:"unsafeCorrectMicro"`
	UnsafeIncorrectMicro int64  `json:"unsafeIncorrectMicro"`
	SafeCorrectMicro     int64  `json:"safeCorrectMicro"`
	SafeIncorrectMicro   int64  `json:"safeIncorrectMicro"`
	GUnsafePPM           int64  `json:"gUnsafePpm"`
	GSafePPM             int64  `json:"gSafePpm"`
	Version              int64  `json:"version"`
	RegisteredAtUnix     int64  `json:"registeredAtUnix"`
	LastSettlementAtUnix int64  `json:"lastSettlementAtUnix,omitempty"`
}

type ValidatorState struct {
	ValidatorID          string `json:"validatorId"`
	PublicKeyHex         string `json:"publicKeyHex"`
	PublicKeyFingerprint string `json:"publicKeyFingerprint"`
	ReliabilityPPM       int64  `json:"reliabilityPpm"`
	Version              int64  `json:"version"`
	RegisteredAtUnix     int64  `json:"registeredAtUnix"`
}

type SignedJudgeOutput struct {
	JudgeID        string `json:"judgeId"`
	DecisionID     string `json:"decisionId"`
	SampleID       string `json:"sampleId"`
	Label          string `json:"label"`
	EvidenceSHA256 string `json:"evidenceSha256"`
	PolicySHA256   string `json:"policySha256"`
	AdapterVersion string `json:"adapterVersion"`
	SignatureHex   string `json:"signatureHex"`
}

type DecisionRecord struct {
	SampleID           string              `json:"sampleId"`
	EvidenceSHA256     string              `json:"evidenceSha256"`
	JudgeOutputs       []SignedJudgeOutput `json:"judgeOutputs"`
	ProvisionalLabel   string              `json:"provisionalLabel"`
	PosteriorUnsafePPM int64               `json:"posteriorUnsafePpm"`
}

type FrozenJudgeReliability struct {
	JudgeID    string `json:"judgeId"`
	GUnsafePPM int64  `json:"gUnsafePpm"`
	GSafePPM   int64  `json:"gSafePpm"`
	Version    int64  `json:"version"`
}

type CommitteeVote struct {
	DecisionID       string `json:"decisionId"`
	DecisionDigest   string `json:"decisionDigest"`
	ValidatorID      string `json:"validatorId"`
	VoteType         string `json:"voteType"`
	ValidatorVersion int64  `json:"validatorVersion"`
	SignatureHex     string `json:"signatureHex"`
	CreatedAtUnix    int64  `json:"createdAtUnix"`
}

type CertificateSigner struct {
	ValidatorID      string `json:"validatorId"`
	ValidatorVersion int64  `json:"validatorVersion"`
	SignatureHex     string `json:"signatureHex"`
}

type RGGCertificate struct {
	DecisionID             string               `json:"decisionId"`
	DecisionDigest         string               `json:"decisionDigest"`
	View                   int64                `json:"view"`
	Sequence               int64                `json:"sequence"`
	Signers                []CertificateSigner  `json:"signers"`
	ProtocolMessages       []RGGProtocolMessage `json:"protocolMessages"`
	ProtocolCertificateSHA string               `json:"protocolCertificateSha256"`
	CertificateSHA         string               `json:"certificateSha256"`
	Verified               bool                 `json:"verified"`
	CreatedAtUnix          int64                `json:"createdAtUnix"`
}

type RGGProtocolMessage struct {
	Type      string          `json:"type"`
	Sender    int64           `json:"sender"`
	View      int64           `json:"view"`
	Sequence  int64           `json:"sequence"`
	Digest    string          `json:"digest"`
	Group     int64           `json:"group"`
	Payload   json.RawMessage `json:"payload"`
	Signature string          `json:"signature"`
}

type DecisionSnapshot struct {
	DecisionID          string                   `json:"decisionId"`
	SampleID            string                   `json:"sampleId"`
	EvidenceCID         string                   `json:"evidenceCid"`
	EvidenceSHA256      string                   `json:"evidenceSha256"`
	Records             []DecisionRecord         `json:"records,omitempty"`
	JudgeOutputs        []SignedJudgeOutput      `json:"judgeOutputs"`
	FrozenReliabilities []FrozenJudgeReliability `json:"frozenReliabilities"`
	ProvisionalLabel    string                   `json:"provisionalLabel"`
	PosteriorUnsafePPM  int64                    `json:"posteriorUnsafePpm"`
	DecisionDigest      string                   `json:"decisionDigest"`
	CommitteeQuorum     int64                    `json:"committeeQuorum"`
	CertificateQuorum   int64                    `json:"certificateQuorum"`
	LeaderValidatorIDs  []string                 `json:"leaderValidatorIds"`
	DeadlineUnix        int64                    `json:"deadlineUnix"`
	AckCount            int64                    `json:"ackCount"`
	ObjectCount         int64                    `json:"objectCount"`
	Status              string                   `json:"status"`
	Certificate         *RGGCertificate          `json:"certificate,omitempty"`
	SettlementLabel     string                   `json:"settlementLabel,omitempty"`
	Settled             bool                     `json:"settled"`
	CreatedAtUnix       int64                    `json:"createdAtUnix"`
	ConfirmedAtUnix     int64                    `json:"confirmedAtUnix,omitempty"`
	CertifiedAtUnix     int64                    `json:"certifiedAtUnix,omitempty"`
	SettledAtUnix       int64                    `json:"settledAtUnix,omitempty"`
}

type RegisterJudgeInput struct {
	JudgeID              string `json:"judgeId"`
	Organization         string `json:"organization"`
	ModelID              string `json:"modelId"`
	ModelRevision        string `json:"modelRevision"`
	PolicySHA256         string `json:"policySha256"`
	AdapterVersion       string `json:"adapterVersion"`
	PublicKeyHex         string `json:"publicKeyHex"`
	UnsafeCorrectMicro   int64  `json:"unsafeCorrectMicro,omitempty"`
	UnsafeIncorrectMicro int64  `json:"unsafeIncorrectMicro,omitempty"`
	SafeCorrectMicro     int64  `json:"safeCorrectMicro,omitempty"`
	SafeIncorrectMicro   int64  `json:"safeIncorrectMicro,omitempty"`
}

type RegisterValidatorInput struct {
	ValidatorID    string `json:"validatorId"`
	PublicKeyHex   string `json:"publicKeyHex"`
	ReliabilityPPM int64  `json:"reliabilityPpm"`
	Version        int64  `json:"version"`
}

type FreezeDecisionInput struct {
	DecisionID         string              `json:"decisionId"`
	SampleID           string              `json:"sampleId"`
	EvidenceCID        string              `json:"evidenceCid"`
	EvidenceSHA256     string              `json:"evidenceSha256"`
	JudgeOutputs       []SignedJudgeOutput `json:"judgeOutputs"`
	ProvisionalLabel   string              `json:"provisionalLabel"`
	PosteriorUnsafePPM int64               `json:"posteriorUnsafePpm"`
	CommitteeQuorum    int64               `json:"committeeQuorum"`
	CertificateQuorum  int64               `json:"certificateQuorum"`
	LeaderValidatorIDs []string            `json:"leaderValidatorIds"`
	DeadlineUnix       int64               `json:"deadlineUnix"`
}

type FreezeDecisionBatchInput struct {
	DecisionID         string           `json:"decisionId"`
	EvidenceCID        string           `json:"evidenceCid"`
	EvidenceSHA256     string           `json:"evidenceSha256"`
	Records            []DecisionRecord `json:"records"`
	CommitteeQuorum    int64            `json:"committeeQuorum"`
	CertificateQuorum  int64            `json:"certificateQuorum"`
	LeaderValidatorIDs []string         `json:"leaderValidatorIds"`
	DeadlineUnix       int64            `json:"deadlineUnix"`
}

type SubmitCommitteeVoteInput struct {
	DecisionID       string `json:"decisionId"`
	DecisionDigest   string `json:"decisionDigest"`
	ValidatorID      string `json:"validatorId"`
	VoteType         string `json:"voteType"`
	ValidatorVersion int64  `json:"validatorVersion"`
	SignatureHex     string `json:"signatureHex"`
}

type CertifyDecisionInput struct {
	DecisionID       string               `json:"decisionId"`
	DecisionDigest   string               `json:"decisionDigest"`
	View             int64                `json:"view"`
	Sequence         int64                `json:"sequence"`
	ProtocolMessages []RGGProtocolMessage `json:"protocolMessages"`
}

type SettleDecisionInput struct {
	DecisionID        string            `json:"decisionId"`
	IndependentLabel  string            `json:"independentLabel,omitempty"`
	IndependentLabels []SettlementLabel `json:"independentLabels,omitempty"`
}

type SettlementLabel struct {
	SampleID string `json:"sampleId"`
	Label    string `json:"label"`
}
