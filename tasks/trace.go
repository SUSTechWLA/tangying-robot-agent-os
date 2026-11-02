package tasks

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

// Trace is a read-only diagnostic projection. It never authorizes execution or
// upgrades a recorded completion to a current physical fact.
type Trace struct {
	SchemaVersion   string                `json:"schemaVersion"`
	TaskID          string                `json:"taskId"`
	Adapter         string                `json:"adapter"`
	State           taskgraph.TaskState   `json:"state"`
	CurrentRevision uint64                `json:"currentRevision"`
	OriginalRequest TraceRequest          `json:"originalRequest"`
	CurrentRequest  string                `json:"currentRequest"`
	Session         *agentsession.Binding `json:"session,omitempty"`
	Source          TraceSource           `json:"source"`
	Integrity       TraceIntegrity        `json:"integrity"`
	Steps           []TraceStep           `json:"steps"`
	RPCCalls        []TraceRPC            `json:"rpcCalls"`
	Children        []TraceChild          `json:"children"`
	Revisions       []RevisionRecord      `json:"revisions"`
	Events          []TaskEvent           `json:"events"`
	Evidence        []EvidenceRecord      `json:"evidence"`
}

type TraceRequest struct {
	Text     string `json:"text"`
	SHA256   string `json:"sha256"`
	Revision uint64 `json:"revision"`
	Source   string `json:"source"`
}

type TraceSource struct {
	EventCount         int    `json:"eventCount"`
	LastSequence       uint64 `json:"lastSequence"`
	AggregateVersion   uint64 `json:"aggregateVersion"`
	Stable             bool   `json:"stable"`
	SnapshotSHA256     string `json:"snapshotSha256"`
	EvidenceAvailable  bool   `json:"evidenceAvailable"`
	EvidenceTruncated  bool   `json:"evidenceTruncated"`
	EvidenceNextBefore int64  `json:"evidenceNextBefore,omitempty"`
	EvidenceScope      string `json:"evidenceScope"`
	ConsistencyScope   string `json:"consistencyScope"`
}

type TraceIntegrity struct {
	Status   string         `json:"status"`
	Findings []TraceFinding `json:"findings"`
}

type TraceFinding struct {
	TaskID         string   `json:"taskId"`
	Code           string   `json:"code"`
	Severity       string   `json:"severity"`
	Message        string   `json:"message"`
	EventSequences []uint64 `json:"eventSequences"`
	StepKey        string   `json:"stepKey,omitempty"`
}

// A step is a command lifecycle, not merely a display name. Different robots,
// revisions and command attempts must never close one another's lifecycle.
type TraceStep struct {
	Key                    string    `json:"key"`
	TaskRevision           uint64    `json:"taskRevision"`
	StepID                 string    `json:"stepId"`
	CommandID              string    `json:"commandId"`
	RobotID                string    `json:"robotId"`
	Tool                   string    `json:"tool"`
	Status                 string    `json:"status"`
	EventSequences         []uint64  `json:"eventSequences"`
	EvidenceIDs            []string  `json:"evidenceIds"`
	MutatesWorld           *bool     `json:"mutatesWorld"`
	OutcomeUnknown         bool      `json:"outcomeUnknown"`
	FirstRecordedAt        time.Time `json:"firstRecordedAt"`
	LastRecordedAt         time.Time `json:"lastRecordedAt"`
	started                bool
	contractVerified       bool
	capabilityCall         bool
	capabilityReceipt      bool
	capabilityVerification bool
}

var ErrTraceBusy = errors.New("task trace changed while being read; retry")

// Trace reads the task and immutable revisions twice around evidence metadata.
// A moving journal is retried three times, never returned as a coherent snapshot.
// This relies on the existing single owner / append-only repository contract;
// it is not a cross-host transaction or cryptographic attestation.
func (s *Service) Trace(ctx context.Context, id string, evidence EvidenceStore) (*Trace, error) {
	for attempt := 0; attempt < 3; attempt++ {
		task, err := s.store.Get(ctx, id)
		if err != nil {
			return nil, err
		}
		history, err := s.store.ListRevisions(ctx, id)
		if err != nil {
			return nil, err
		}
		children, err := s.traceChildren(ctx, task)
		if err != nil {
			return nil, err
		}
		before, err := traceDigest([]any{task, history, children})
		if err != nil {
			return nil, err
		}
		var records []EvidenceRecord
		var cursor int64
		truncated := false
		if evidence != nil {
			// Expired metadata is intentionally retained by storage; bound export
			// memory without claiming that an omitted old capture never existed.
			for page := 0; page < 20; page++ {
				batch, err := evidence.ListEvidence(ctx, id, 200, cursor)
				if err != nil {
					return nil, err
				}
				if len(batch) > 200 {
					return nil, ErrEvidenceCorrupt
				}
				for _, record := range batch {
					if record.TaskID != id || record.RecordIndex <= 0 || (cursor != 0 && record.RecordIndex >= cursor) {
						return nil, ErrEvidenceCorrupt
					}
					cursor = record.RecordIndex
					record.Snapshot, record.RGB, record.Depth = nil, nil, nil
					records = append(records, record)
				}
				if len(batch) < 200 {
					break
				}
				if page == 19 {
					remaining, readErr := evidence.ListEvidence(ctx, id, 1, cursor)
					if readErr != nil {
						return nil, readErr
					}
					if len(remaining) > 1 || (len(remaining) == 1 && (remaining[0].TaskID != id || remaining[0].RecordIndex <= 0 || remaining[0].RecordIndex >= cursor)) {
						return nil, ErrEvidenceCorrupt
					}
					truncated = len(remaining) > 0
				}
			}
		}
		afterTask, err := s.store.Get(ctx, id)
		if err != nil {
			return nil, err
		}
		afterHistory, err := s.store.ListRevisions(ctx, id)
		if err != nil {
			return nil, err
		}
		afterChildren, err := s.traceChildren(ctx, afterTask)
		if err != nil {
			return nil, err
		}
		after, err := traceDigest([]any{afterTask, afterHistory, afterChildren})
		if err != nil {
			return nil, err
		}
		if before != after {
			continue
		}
		result := BuildTrace(*task, history, records, evidence != nil, truncated)
		result.Children = children
		for _, child := range children {
			parentRevisionFound := child.ParentRevision == 0
			for _, record := range history {
				if record.Revision.Revision == child.ParentRevision && record.Revision.TaskID == task.ID {
					parentRevisionFound = true
					break
				}
			}
			if !parentRevisionFound {
				result.Integrity.Findings = append(result.Integrity.Findings, TraceFinding{TaskID: task.ID, Code: "CHILD_PARENT_REVISION_MISSING", Severity: "warning", Message: "子计划声明的父版本缺少不可变版本记录：" + child.ChildID, EventSequences: child.ParentEventSequences})
				if result.Integrity.Status == "consistent" {
					result.Integrity.Status = "incomplete"
				}
			}
			for _, finding := range child.Findings {
				finding.Message = "子计划 " + child.ChildID + "：" + finding.Message
				result.Integrity.Findings = append(result.Integrity.Findings, finding)
				if finding.Severity == "error" {
					result.Integrity.Status = "inconsistent"
				} else if finding.Severity != "info" && result.Integrity.Status == "consistent" {
					result.Integrity.Status = "incomplete"
				}
			}
		}
		result.Source.Stable = true
		if truncated {
			result.Source.EvidenceNextBefore = cursor
		}
		result.Source.SnapshotSHA256, err = traceDigest([]any{task, history, records, children})
		if err != nil {
			return nil, err
		}
		return &result, nil
	}
	return nil, ErrTraceBusy
}

func traceDigest(value any) (string, error) {
	wire, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(wire)
	return hex.EncodeToString(sum[:]), nil
}

// BuildTrace preserves ledger order (sequence, not wall-clock sorting). It uses
// the same identity and mirror rules as the context memory projector.
func BuildTrace(task Task, history []RevisionRecord, evidence []EvidenceRecord, available, truncated bool) Trace {
	t := Trace{SchemaVersion: "task.trace.v1", TaskID: task.ID, Adapter: task.Adapter,
		State: task.State, CurrentRevision: task.CurrentRevision, CurrentRequest: task.Request,
		Source: TraceSource{EventCount: len(task.Events), AggregateVersion: task.AggregateVersion,
			EvidenceAvailable: available, EvidenceTruncated: truncated, EvidenceScope: "capture metadata only; raw content checksums require observation retrieval",
			ConsistencyScope: "task, revisions and declared child aggregates checked twice; evidence metadata is paginated best-effort"},
		Integrity: TraceIntegrity{Status: "consistent", Findings: []TraceFinding{}}, Steps: []TraceStep{},
		Revisions: append([]RevisionRecord{}, history...), Events: append([]TaskEvent{}, task.Events...), Evidence: append([]EvidenceRecord{}, evidence...)}
	add := func(code, severity, message, key string, sequences ...uint64) {
		if sequences == nil {
			sequences = []uint64{}
		}
		t.Integrity.Findings = append(t.Integrity.Findings, TraceFinding{TaskID: task.ID, Code: code, Severity: severity, Message: message, EventSequences: sequences, StepKey: key})
		if severity == "error" {
			t.Integrity.Status = "inconsistent"
		} else if t.Integrity.Status == "consistent" {
			t.Integrity.Status = "incomplete"
		}
	}
	revisions := map[uint64]TaskRevision{}
	for _, record := range history {
		r := record.Revision
		if r.TaskID != task.ID || r.Revision == 0 {
			add("REVISION_BINDING_INVALID", "error", "计划版本不属于本任务或版本号无效", "")
			continue
		}
		if _, exists := revisions[r.Revision]; exists {
			add("REVISION_DUPLICATE", "error", "同一版本存在多份记录", "")
		}
		revisions[r.Revision] = r
		if r.Revision == 1 {
			sum := sha256.Sum256([]byte(r.Request))
			t.OriginalRequest = TraceRequest{r.Request, hex.EncodeToString(sum[:]), 1, "revision:1"}
		}
	}
	if t.OriginalRequest.Source == "" {
		add("ORIGINAL_REQUEST_MISSING", "warning", "缺少不可变首版；不能把当前修改后的目标冒充原始指令", "")
	}
	if current, ok := revisions[task.CurrentRevision]; !ok {
		add("CURRENT_REVISION_MISSING", "warning", "当前任务缺少对应计划版本", "")
	} else if current.Request != task.Request {
		add("CURRENT_REQUEST_MISMATCH", "error", "任务当前目标与对应计划版本不一致", "")
	}
	byKey := map[string]int{}
	seenIDs := map[string]TaskEvent{}
	commandBindings := map[string]string{}
	var previous uint64
	var state string
	created := 0
	for index, event := range task.Events {
		if event.Sequence == 0 || event.Sequence != previous+1 {
			add("EVENT_SEQUENCE_INVALID", "error", "账本事件序号重复、缺口或乱序", "", event.Sequence)
		}
		previous = event.Sequence
		t.Source.LastSequence = event.Sequence
		if event.OccurredAt.IsZero() {
			add("EVENT_TIME_MISSING", "warning", "事件缺少记录时间", "", event.Sequence)
		}
		if index > 0 && event.OccurredAt.Before(task.Events[index-1].OccurredAt) {
			add("EVENT_CLOCK_REGRESSION", "warning", "记录时间回退；因果顺序仍以账本序号为准", "", task.Events[index-1].Sequence, event.Sequence)
		}
		if event.Type == "TASK_CREATED" {
			created++
			if raw, exists := event.Payload["session"]; exists {
				wire, err := json.Marshal(raw)
				var binding agentsession.Binding
				if err != nil || json.Unmarshal(wire, &binding) != nil || binding.Validate() != nil || binding.TaskID != task.ID || binding.Adapter != task.Adapter {
					add("SESSION_BINDING_INVALID", "error", "会话身份或上下文校验失败", "", event.Sequence)
				} else {
					t.Session = &binding
					if robot := revisions[1].Intent.RobotID; robot != "" && binding.RobotID != robot {
						add("SESSION_ROBOT_MISMATCH", "error", "会话机器人与不可变首版计划不同", "", event.Sequence)
					}
				}
			}
		}
		if event.Type == "STATE_CHANGED" {
			from, to := contextString(event.Payload, "previousState"), contextString(event.Payload, "state")
			if from == "" || to == "" {
				add("STATE_TRANSITION_INCOMPLETE", "warning", "旧状态事件缺少前态或后态，无法完整还原", "", event.Sequence)
				state = ""
			} else {
				if (to != string(taskgraph.StateSafetyStopped) && !taskgraph.CanTransition(taskgraph.TaskState(from), taskgraph.TaskState(to))) || terminal(taskgraph.TaskState(from)) {
					add("STATE_TRANSITION_INVALID", "error", "账本包含状态机不允许的变化", "", event.Sequence)
				}
				if state != "" && state != from {
					add("STATE_CHAIN_BROKEN", "error", "相邻状态变化无法衔接", "", event.Sequence)
				}
				state = to
			}
		}
		status := contextActionStatus(event)
		if status == "" {
			if event.Type == "TOOL_ACTIVITY" || event.Type == "action.executed" {
				add("STEP_STATUS_MISSING", "warning", "工具活动缺少状态，不能判定执行结果", "", event.Sequence)
			}
			continue
		}
		if id := contextString(event.Payload, "eventId"); id != "" {
			if prior, exists := seenIDs[id]; exists {
				if !contextSameEvent(prior, event) {
					add("EVENT_ID_CONFLICT", "error", "相同事件身份携带不同内容", "", prior.Sequence, event.Sequence)
				} else {
					continue
				}
			} else {
				seenIDs[id] = event
			}
		}
		scope := contextEventScope(task.ID, event)
		step, command, tool := contextStepID(event), contextString(event.Payload, "commandId"), contextString(event.Payload, "toolName", "tool")
		identity := []any{scope.PlanRevision, scope.RobotID, step, command, tool}
		key, _ := traceDigest(identity)
		if scope.PlanRevision == 0 || scope.RobotID == "" || step == "" || command == "" || tool == "" {
			key = fmt.Sprintf("unbound:%d:%d", event.Sequence, index)
			add("STEP_BINDING_INCOMPLETE", "warning", "步骤缺少完整版本、机器人或命令身份；单独保留，不与其他记录合并", key, event.Sequence)
		} else if _, ok := revisions[uint64(scope.PlanRevision)]; !ok {
			add("STEP_REVISION_MISSING", "warning", "原子步骤指向不存在的计划版本", key, event.Sequence)
		}
		if command != "" && scope.PlanRevision != 0 && scope.RobotID != "" && step != "" && tool != "" {
			if prior, exists := commandBindings[command]; exists && prior != key {
				add("COMMAND_BINDING_CONFLICT", "error", "同一命令指向不同执行身份", key, event.Sequence)
			} else {
				commandBindings[command] = key
			}
		}
		position, exists := byKey[key]
		if !exists {
			position = len(t.Steps)
			byKey[key] = position
			t.Steps = append(t.Steps, TraceStep{Key: key, TaskRevision: uint64(scope.PlanRevision), RobotID: scope.RobotID, StepID: step, CommandID: command, Tool: tool, EvidenceIDs: []string{}, EventSequences: []uint64{}, FirstRecordedAt: event.OccurredAt})
		}
		s := &t.Steps[position]
		s.EventSequences = append(s.EventSequences, event.Sequence)
		if s.Status == "CONFIRMED" && status != "CONFIRMED" {
			add("STEP_COMPLETION_REVISED", "warning", "同一命令的完成记录后又出现其他状态；保留两端记录调查原因", key, s.EventSequences...)
		}
		s.LastRecordedAt, s.Status = event.OccurredAt, status
		if value, ok := event.Payload["mutatesWorld"].(bool); ok {
			if s.MutatesWorld != nil && *s.MutatesWorld != value {
				add("STEP_EFFECT_CONFLICT", "error", "同一命令的物理效果声明不一致", key, event.Sequence)
			}
			s.MutatesWorld = &value
		}
		if status == "SENDING" || status == "RUNNING" {
			s.started = true
		}
		if event.Type == "CAPABILITY_CALL" {
			s.capabilityCall = true
		}
		if event.Type == "CAPABILITY_RECEIPT" {
			s.capabilityReceipt = true
		}
		if event.Type == "CAPABILITY_VERIFIED" {
			s.capabilityVerification = true
			proof, ok := event.Payload["evidence"].(map[string]any)
			s.contractVerified = ok && len(proof) > 0 && s.capabilityCall && (s.capabilityReceipt || (tool == "robot.task" && contextString(proof, "basis") == "LEGACY_RUNNER_VERIFIED_CHILD_STEPS"))
			if !s.contractVerified {
				add("CAPABILITY_VERIFICATION_CHAIN_INCOMPLETE", "warning", "能力完成记录缺少有效契约证据或先前调用/回执", key, event.Sequence)
			}
		}
		if eventBool(event.Payload["outcomeUnknown"]) {
			s.OutcomeUnknown = true
		}
		for _, id := range eventStrings(event.Payload["evidenceIds"]) {
			found := false
			for _, existing := range s.EvidenceIDs {
				if existing == id {
					found = true
					break
				}
			}
			if !found {
				s.EvidenceIDs = append(s.EvidenceIDs, id)
			}
		}
	}
	if created != 1 {
		add("CREATION_EVENT_MISSING_OR_DUPLICATE", "warning", "任务应有且仅有一条创建记录", "")
	}
	if state != "" && state != string(task.State) {
		add("STATE_SNAPSHOT_MISMATCH", "error", "末条状态变化与当前持久状态不同", "")
	}
	if !available {
		add("EVIDENCE_STORE_UNAVAILABLE", "warning", "未配置历史传感器证据存储，不能检查观测引用", "")
	}
	if truncated {
		add("EVIDENCE_EXPORT_TRUNCATED", "warning", "证据元数据超过导出上限；其余记录按游标读取", "")
	}
	for i := range t.Evidence {
		t.Evidence[i].Snapshot, t.Evidence[i].RGB, t.Evidence[i].Depth = nil, nil, nil
	}
	evidenceIndex := map[string][]EvidenceRecord{}
	for _, record := range evidence {
		evidenceIndex[record.ID] = append(evidenceIndex[record.ID], record)
		if record.CaptureID != record.ID {
			evidenceIndex[record.CaptureID] = append(evidenceIndex[record.CaptureID], record)
		}
	}
	for _, s := range t.Steps {
		if s.Status == "CONFIRMED" && !s.started {
			add("STEP_START_MISSING", "warning", "有完成记录但找不到同一命令的开始记录", s.Key, s.EventSequences...)
		}
		if s.OutcomeUnknown {
			add("STEP_OUTCOME_UNKNOWN", "warning", "此命令曾记录结果未知；本诊断不清除对账要求", s.Key, s.EventSequences...)
		}
		if s.MutatesWorld == nil {
			add("STEP_EFFECT_UNKNOWN", "warning", "缺少工具效果声明，不能假定只读或已物理验证", s.Key, s.EventSequences...)
		}
		switch s.Status {
		case "SENDING", "RUNNING", "AWAITING_EVIDENCE", "CONFIRMED", "FAILED", "PREPARATION_FAILED", "CANCELLED":
		default:
			add("STEP_STATUS_UNKNOWN", "warning", "无法识别工具活动状态："+s.Status, s.Key, s.EventSequences...)
		}
		if s.Status == "CONFIRMED" && s.MutatesWorld != nil && *s.MutatesWorld && len(s.EvidenceIDs) == 0 && !s.contractVerified {
			add("STEP_COMPLETION_EVIDENCE_MISSING", "warning", "改变世界的步骤缺少关联观测或能力契约复验", s.Key, s.EventSequences...)
		}
		if task.State == taskgraph.StateSucceeded && s.TaskRevision == task.CurrentRevision && (s.Status == "RUNNING" || s.Status == "SENDING" || s.Status == "AWAITING_EVIDENCE") {
			add("SUCCESS_WITH_OPEN_COMMAND", "error", "任务成功但当前版本仍有未收尾命令", s.Key, s.EventSequences...)
		}
		for _, id := range s.EvidenceIDs {
			if !available {
				continue
			}
			matches := evidenceIndex[id]
			valid := []EvidenceRecord{}
			for _, record := range matches {
				stepMatches := record.StepID == s.StepID || (s.TaskRevision > 1 && record.StepID == fmt.Sprintf("revision-%d/%s", s.TaskRevision, s.StepID))
				if record.TaskID == task.ID && stepMatches && record.TaskRevision == s.TaskRevision && record.RobotID == s.RobotID {
					valid = append(valid, record)
				}
			}
			if len(valid) != 1 {
				severity, code := "warning", "EVIDENCE_REFERENCE_MISSING"
				if len(matches) > 0 {
					severity, code = "error", "EVIDENCE_BINDING_CONFLICT"
				}
				add(code, severity, "观测引用无法唯一匹配任务、版本、步骤与机器人："+id, s.Key, s.EventSequences...)
				continue
			}
			record := valid[0]
			if record.Expired {
				add("EVIDENCE_EXPIRED", "warning", "观测原始数据已过保留期；仅保留元数据与摘要："+id, s.Key, s.EventSequences...)
			}
			// RecordedAt and ObservedAt belong to different clocks; report, do
			// not claim a physical impossibility or compute a fabricated latency.
			if record.ObservedAtUnixMS > 0 && record.ObservedAtUnixMS < s.FirstRecordedAt.UnixMilli() && s.MutatesWorld != nil && *s.MutatesWorld {
				add("EVIDENCE_PREDATES_COMMAND", "warning", "观测时间早于命令记录；检查证据用途和时钟对齐："+id, s.Key, s.EventSequences...)
			}
		}
	}
	if task.State == taskgraph.StateSucceeded && len(t.Steps) == 0 {
		add("SUCCESS_WITHOUT_EXECUTION_TRACE", "warning", "成功任务没有可关联的原子执行记录", "")
	}
	if task.State == taskgraph.StateSucceeded {
		if plan := revisions[task.CurrentRevision].Plan; plan != nil && plan.Capabilities != nil {
			for i, call := range plan.Capabilities.Calls {
				id := fmt.Sprintf("rev-%d-cap-%02d", task.CurrentRevision, i+1)
				found := false
				for _, step := range t.Steps {
					if step.TaskRevision == task.CurrentRevision && step.StepID == id && step.RobotID == plan.Capabilities.RobotID && step.Tool == call.Tool && step.Status == "CONFIRMED" {
						found = true
						break
					}
				}
				if !found {
					add("SUCCESS_WITH_UNCONFIRMED_PLAN_STEP", "error", "成功任务的冻结能力计划没有对应完成记录："+id, "")
				}
			}
		}
	}
	t.RPCCalls = traceRPCs(task, t.Steps, revisions, add)
	sort.SliceStable(t.Revisions, func(i, j int) bool { return t.Revisions[i].Revision.Revision < t.Revisions[j].Revision.Revision })
	return t
}
