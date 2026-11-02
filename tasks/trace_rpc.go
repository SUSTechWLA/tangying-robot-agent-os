package tasks

import "time"

// RPC records are attempts inside a parent command, including status polls,
// verification and cancellation. A successful RPC is not a completed command.
type TraceRPC struct {
	RPCID             string    `json:"rpcId"`
	Service           string    `json:"service"`
	RequestID         string    `json:"requestId"`
	OperationID       string    `json:"operationId,omitempty"`
	OperationOwnerID  string    `json:"operationOwnerId,omitempty"`
	CommandID         string    `json:"commandId"`
	TaskRevision      uint64    `json:"taskRevision"`
	RobotID           string    `json:"robotId"`
	StepID            string    `json:"stepId"`
	Phase             string    `json:"phase"`
	Status            string    `json:"status"`
	EventSequences    []uint64  `json:"eventSequences"`
	StartedAt         time.Time `json:"startedAt"`
	FinishedAt        time.Time `json:"finishedAt"`
	started, finished bool
	identity          string
}

func traceRPCs(task Task, steps []TraceStep, revisions map[uint64]TaskRevision, add func(string, string, string, string, ...uint64)) []TraceRPC {
	calls := []TraceRPC{}
	index := map[string]int{}
	for _, event := range task.Events {
		if event.Type != "CAPABILITY_RPC_STARTED" && event.Type != "CAPABILITY_RPC_FINISHED" {
			continue
		}
		scope := contextEventScope(task.ID, event)
		id := contextString(event.Payload, "rpcId")
		service, request, command := contextString(event.Payload, "service"), contextString(event.Payload, "requestId"), contextString(event.Payload, "commandId")
		step := contextStepID(event)
		if id == "" || service == "" || command == "" || step == "" || scope.PlanRevision == 0 || scope.RobotID == "" {
			add("RPC_BINDING_INCOMPLETE", "warning", "底层服务调用缺少完整身份；不能确认调用链", "", event.Sequence)
		}
		key := id
		if key == "" {
			key = contextEventID(event, len(calls))
		}
		phase, operation, owner := contextString(event.Payload, "phase"), contextString(event.Payload, "operationId"), contextString(event.Payload, "operationOwnerId")
		identity, _ := traceDigest([]any{service, request, command, step, scope.PlanRevision, scope.RobotID, phase, operation, owner})
		position, exists := index[key]
		if !exists {
			position = len(calls)
			index[key] = position
			calls = append(calls, TraceRPC{RPCID: id, Service: service, RequestID: request, OperationID: operation, OperationOwnerID: owner, CommandID: command, StepID: step, RobotID: scope.RobotID, TaskRevision: uint64(scope.PlanRevision), Phase: phase, identity: identity, Status: "STARTED"})
		}
		call := &calls[position]
		call.EventSequences = append(call.EventSequences, event.Sequence)
		if call.identity != identity {
			add("RPC_BINDING_CONFLICT", "error", "同一 RPC 身份指向不同调用", "", call.EventSequences...)
			continue
		}
		if event.Type == "CAPABILITY_RPC_STARTED" {
			if call.started || call.finished {
				add("RPC_START_ORDER_INVALID", "error", "RPC 开始记录重复或晚于结果", "", call.EventSequences...)
			}
			call.started = true
			call.StartedAt = event.OccurredAt
		} else {
			if !call.started {
				add("RPC_START_MISSING", "warning", "RPC 结果没有对应开始记录", "", call.EventSequences...)
			}
			if call.finished {
				add("RPC_FINISH_DUPLICATE", "error", "RPC 结果重复", "", call.EventSequences...)
			}
			call.finished = true
			call.FinishedAt = event.OccurredAt
			call.Status = contextString(event.Payload, "status")
			if call.Status != "SUCCEEDED" && call.Status != "FAILED" {
				call.Status = "UNKNOWN"
				add("RPC_STATUS_INVALID", "warning", "RPC 结束记录缺少可识别的结果状态", "", call.EventSequences...)
			}
		}
	}
	for _, call := range calls {
		if _, ok := revisions[call.TaskRevision]; !ok {
			add("RPC_REVISION_MISSING", "warning", "底层调用指向不存在的计划版本", "", call.EventSequences...)
		}
		parent := false
		for _, step := range steps {
			if step.TaskRevision == call.TaskRevision && step.RobotID == call.RobotID && step.StepID == call.StepID && step.CommandID == call.CommandID {
				parent = true
				break
			}
		}
		if !parent {
			add("RPC_PARENT_MISSING", "warning", "底层调用找不到同一版本、机器人、步骤及命令的父记录", "", call.EventSequences...)
		}
		if !call.finished {
			add("RPC_RESULT_MISSING", "warning", "RPC 已登记开始但没有持久结果；不能推断服务未执行", "", call.EventSequences...)
		}
	}
	return calls
}
