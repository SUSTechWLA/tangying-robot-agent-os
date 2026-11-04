package tasks

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
)

const maxTraceChildren = 64

// TraceChild reads the authoritative child aggregate, not the advisory parent
// mirror. Child.Request is the generated bounded goal, never a substitute for
// the user's original natural-language request in the parent revision ledger.
type TraceChild struct {
	ParentTaskID         string         `json:"parentTaskId"`
	ChildID              string         `json:"childId"`
	ParentRevision       uint64         `json:"parentRevision"`
	PlanDigest           string         `json:"planDigest"`
	ParentEventSequences []uint64       `json:"parentEventSequences"`
	AggregateVersion     uint64         `json:"aggregateVersion"`
	SnapshotSHA256       string         `json:"snapshotSha256,omitempty"`
	Found                bool           `json:"found"`
	BindingVerified      bool           `json:"bindingVerified"`
	Coverage             string         `json:"coverage"`
	CoverageTruncated    bool           `json:"coverageTruncated"`
	OmittedReferences    int            `json:"omittedReferences,omitempty"`
	RequestSource        string         `json:"requestSource"`
	Task                 *Task          `json:"task,omitempty"`
	Steps                []TraceStep    `json:"steps"`
	RPCCalls             []TraceRPC     `json:"rpcCalls"`
	Findings             []TraceFinding `json:"findings"`
}

func (c *TraceChild) finding(code, severity, message string, sequences ...uint64) {
	if sequences == nil {
		sequences = []uint64{}
	}
	c.Findings = append(c.Findings, TraceFinding{TaskID: c.ParentTaskID, Code: code, Severity: severity, Message: message, EventSequences: sequences})
}

// traceChildIdentity checks the producer's exact namespace without trusting a
// declaration to select an arbitrary aggregate belonging to another task.
func traceChildIdentity(parent, child string, revision uint64) bool {
	if parent == "" || revision == 0 {
		return false
	}
	prefix := fmt.Sprintf("%s-edge-r%d-p", parent, revision)
	generation, ok := strings.CutPrefix(child, prefix)
	if !ok {
		return false
	}
	n, err := strconv.ParseUint(generation, 10, 64)
	return err == nil && n > 0 && strconv.FormatUint(n, 10) == generation
}

// traceChildren is deliberately optional for repositories without an aggregate
// store. Missing support, missing declarations and unreadable/mismatched child
// data remain explicit rather than manufacturing a completed execution tree.
// The caller brackets this read with a second read to detect moving aggregates.
func (s *Service) traceChildren(ctx context.Context, task *Task) ([]TraceChild, error) {
	children := []TraceChild{}
	index := map[string]int{}
	declared := map[string]bool{}
	conflicted := map[string]bool{}
	omitted := 0
	for _, event := range task.Events {
		if event.Type != "EDGE_PLAN_CREATED" && event.Type != "EDGE_CHILD_EVENT" {
			continue
		}
		id := contextString(event.Payload, "childId")
		position, exists := index[id]
		if !exists {
			if len(children) == maxTraceChildren {
				omitted++
				continue
			}
			position = len(children)
			index[id] = position
			children = append(children, TraceChild{ParentTaskID: task.ID, ChildID: id, Coverage: "undeclared", RequestSource: "edge-child aggregate generated goal; not original user request",
				ParentEventSequences: []uint64{}, Steps: []TraceStep{}, RPCCalls: []TraceRPC{}, Findings: []TraceFinding{}})
		}
		child := &children[position]
		child.ParentEventSequences = append(child.ParentEventSequences, event.Sequence)
		if event.Type != "EDGE_PLAN_CREATED" {
			continue // Advisory detail never supplies a child receipt or binding.
		}
		revision := uint64(contextRevision(event.Payload["parentRevision"]))
		digest := contextString(event.Payload, "planDigest")
		digestBytes, digestErr := hex.DecodeString(digest)
		if !traceChildIdentity(task.ID, id, revision) || digestErr != nil || len(digestBytes) != 32 {
			conflicted[id] = true
			child.Coverage = "invalid"
			child.finding("CHILD_DECLARATION_INVALID", "error", "子计划声明缺少有效父版本/计划摘要，或子身份越过本任务命名空间；不读取该聚合", event.Sequence)
			continue
		}
		if declared[id] && (child.ParentRevision != revision || child.PlanDigest != digest) {
			conflicted[id] = true
			child.Coverage = "invalid"
			child.finding("CHILD_DECLARATION_CONFLICT", "error", "同一子计划身份对应冲突的父版本或计划摘要；不合并两份执行事实", child.ParentEventSequences...)
			continue
		}
		declared[id] = true
		child.ParentRevision, child.PlanDigest = revision, digest
	}
	if omitted > 0 {
		last := &children[len(children)-1]
		last.CoverageTruncated, last.OmittedReferences = true, omitted
		last.finding("CHILD_TRACE_LIMIT", "warning", fmt.Sprintf("最多展开 %d 个子计划；另有 %d 条父事件引用未展开，不代表相关执行不存在", maxTraceChildren, omitted))
	}
	if len(children) == 0 && task.Plan != nil && task.Plan.Capabilities != nil {
		for _, call := range task.Plan.Capabilities.Calls {
			if call.Tool == objective.Tool {
				child := TraceChild{ParentTaskID: task.ID, Coverage: "undeclared", ParentEventSequences: []uint64{}, Steps: []TraceStep{}, RPCCalls: []TraceRPC{}, Findings: []TraceFinding{}}
				child.finding("CHILD_DECLARATIONS_MISSING", "warning", "父计划包含 edge.goal，但尚无子计划声明；不能推断子计划未运行或已完成")
				children = append(children, child)
				break
			}
		}
	}
	store, supported := s.store.(eventstore.Store)
	for i := range children {
		child := &children[i]
		if conflicted[child.ChildID] {
			continue
		}
		if !declared[child.ChildID] {
			if child.ChildID != "" {
				child.finding("CHILD_DECLARATION_MISSING", "warning", "仅有 advisory 子事件，缺少可验证的子计划声明；不凭镜像拼造执行步骤", child.ParentEventSequences...)
			}
			continue
		}
		if !supported {
			child.Coverage = "unsupported"
			child.finding("CHILD_STORE_UNAVAILABLE", "warning", "当前任务存储未实现 aggregate eventstore，不能读取子计划权威执行账本", child.ParentEventSequences...)
			continue
		}
		key := "edge-child/" + child.ChildID
		state, found, err := store.LoadState(ctx, key)
		if err != nil {
			return nil, fmt.Errorf("read child aggregate %s: %w", child.ChildID, err)
		}
		child.Found = found
		if !found {
			child.Coverage = "missing"
			child.finding("CHILD_AGGREGATE_MISSING", "warning", "父账本已声明子计划，但对应权威聚合不存在；镜像不替代缺失的原始证据", child.ParentEventSequences...)
			continue
		}
		child.AggregateVersion = state.Version
		// Hash raw aggregate bytes even when the stored JSON is corrupt; a
		// corrupt child should remain identifiable in the diagnostic report.
		child.SnapshotSHA256, err = traceDigest([]any{state.AggregateID, state.Version, []byte(state.Data), state.UpdatedAt})
		if err != nil {
			return nil, err
		}
		child.Coverage = "invalid"
		if state.AggregateID != key || state.Version == 0 {
			child.finding("CHILD_AGGREGATE_BINDING_INVALID", "error", "子聚合返回了不同存储身份或无效版本；不导出其他对象的数据", child.ParentEventSequences...)
			continue
		}
		var snapshot Task
		decoder := json.NewDecoder(bytes.NewReader(state.Data))
		decoder.UseNumber()
		if err = decoder.Decode(&snapshot); err != nil {
			child.finding("CHILD_AGGREGATE_INVALID_JSON", "error", "子聚合无法解码为完整 Task 快照", child.ParentEventSequences...)
			continue
		}
		var extra any
		if err = decoder.Decode(&extra); err != io.EOF {
			child.finding("CHILD_AGGREGATE_INVALID_JSON", "error", "子聚合含额外 JSON 内容，不能作为单一快照", child.ParentEventSequences...)
			continue
		}
		if snapshot.ID != child.ChildID || snapshot.CurrentRevision != child.ParentRevision {
			child.finding("CHILD_TASK_BINDING_INVALID", "error", "子 Task 身份或计划版本不匹配父声明；不导出其他任务的数据", child.ParentEventSequences...)
			continue
		}
		child.Task = &snapshot
		if snapshot.Plan == nil || snapshot.Plan.Capabilities == nil || objective.PlanDigest(snapshot.Plan.Capabilities) != child.PlanDigest {
			child.finding("CHILD_PLAN_DIGEST_MISMATCH", "error", "子快照计划与父账本冻结摘要不一致；保留原始快照调查，不据此拼接步骤", child.ParentEventSequences...)
			continue
		}
		child.BindingVerified, child.Coverage = true, "found"
		// There is no independent tasks.TaskRevision ledger for edge-child.
		// Do not manufacture one from today's snapshot to silence diagnostics.
		projection := BuildTrace(snapshot, nil, nil, false, false)
		child.Steps, child.RPCCalls = projection.Steps, projection.RPCCalls
		child.Findings = append(child.Findings, projection.Integrity.Findings...)
		child.finding("CHILD_SNAPSHOT_HISTORY_SCOPE", "warning", "子计划来源是 aggregate 快照及其事件；独立任务版本账本和传感器证据未导入。Request 是边缘规划目标，不是用户原始指令")
	}
	return children, nil
}
