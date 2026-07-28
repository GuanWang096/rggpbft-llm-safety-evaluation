package core

func JudgeStateKey(judgeID string) string {
	return "MJ5JudgeState" + keySeparator + judgeID
}

func ValidatorStateKey(validatorID string) string {
	return "MJ5ValidatorState" + keySeparator + validatorID
}

func DecisionSnapshotKey(decisionID string) string {
	return "MJ5DecisionSnapshot" + keySeparator + decisionID
}

func CommitteeVoteKey(decisionID, validatorID string) string {
	return "MJ5CommitteeVote" + keySeparator + decisionID + keySeparator + validatorID
}
