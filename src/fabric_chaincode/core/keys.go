package core

const keySeparator = "::"

func TaskKey(taskID string) string {
	return "TaskConstraint" + keySeparator + taskID
}

func AllocationKey(taskID string) string {
	return "AllocationDigest" + keySeparator + taskID
}

func ConfirmationKey(taskID string) string {
	return "EvalConfirm" + keySeparator + taskID
}

func VoteKey(taskID, clientID string) string {
	return "EvalVote" + keySeparator + taskID + keySeparator + clientID
}

func EvaluatorKey(evalID string) string {
	return "EvaluatorState" + keySeparator + evalID
}

func OccupancyKey(evalID string) string {
	return "ActiveTaskOccupancy" + keySeparator + evalID
}

func TaskReputationKey(subjectID, taskID string) string {
	return "TaskReputation" + keySeparator + subjectID + keySeparator + taskID
}
