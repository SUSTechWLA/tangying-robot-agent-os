package taskgraph

type TaskState string

const (
	StateReady                 TaskState = "READY"
	StatePaused                TaskState = "PAUSED"
	StateObserving             TaskState = "OBSERVING"
	StatePlanning              TaskState = "PLANNING"
	StateWaitingApproval       TaskState = "WAITING_APPROVAL"
	StateExecuting             TaskState = "EXECUTING"
	StateWaitingForObservation TaskState = "WAITING_FOR_OBSERVATION"
	StateRecovering            TaskState = "RECOVERING"
	StateBlocked               TaskState = "BLOCKED"
	StateFailedSafe            TaskState = "FAILED_SAFE"
	StateVerifying             TaskState = "VERIFYING"
	StateSucceeded             TaskState = "SUCCEEDED"
	StateRecoverableFailure    TaskState = "RECOVERABLE_FAILURE"
	StateSafeRecovery          TaskState = "SAFE_RECOVERY"
	StateWaitingUser           TaskState = "WAITING_USER"
	StateSafetyStopped         TaskState = "SAFETY_STOPPED"
	StateCancelled             TaskState = "CANCELLED"
	StateFailed                TaskState = "FAILED"
)

var stateTransitions = map[TaskState]map[TaskState]bool{
	StateReady:                 {StateObserving: true, StatePaused: true, StateCancelled: true},
	StatePaused:                {StateObserving: true, StateCancelled: true},
	StateObserving:             {StatePlanning: true, StateRecoverableFailure: true, StateWaitingUser: true, StateCancelled: true},
	StatePlanning:              {StateWaitingApproval: true, StateExecuting: true, StateRecoverableFailure: true, StateCancelled: true},
	StateWaitingApproval:       {StateExecuting: true, StateCancelled: true},
	StateExecuting:             {StateVerifying: true, StatePaused: true, StateWaitingForObservation: true, StateRecoverableFailure: true, StateSafetyStopped: true, StateCancelled: true},
	StateWaitingForObservation: {StateRecovering: true, StateBlocked: true, StateCancelled: true},
	StateRecovering:            {StateExecuting: true, StateBlocked: true, StateFailedSafe: true, StateCancelled: true},
	StateVerifying:             {StateSucceeded: true, StateRecoverableFailure: true, StateSafetyStopped: true},
	StateRecoverableFailure:    {StateSafeRecovery: true, StateFailed: true, StateCancelled: true},
	StateSafeRecovery:          {StateObserving: true, StateWaitingUser: true, StateSafetyStopped: true, StateRecoverableFailure: true, StateCancelled: true},
	StateWaitingUser:           {StateObserving: true, StateCancelled: true},
	StateSafetyStopped:         {StateReady: true},
}

func CanTransition(from, to TaskState) bool {
	return stateTransitions[from][to]
}
