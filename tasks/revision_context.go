package tasks

import (
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

// ContextForRevisions adds immutable revision sources to the ledger projection.
// Callers obtain these from Service.ListRevisions; missing history stays unknown.
// Old constraints are retained with their original revision, never silently
// promoted into the currently approved request.
func ContextForRevisions(task Task, revisions []RevisionRecord, role string, now time.Time) agentcontext.Document {
	d := ContextFor(task, role, now)
	ordered := append([]RevisionRecord(nil), revisions...)
	sort.SliceStable(ordered, func(i, j int) bool { return ordered[i].Revision.Revision < ordered[j].Revision.Revision })
	seen := map[uint64]bool{}
	for _, record := range ordered {
		revision := record.Revision
		if revision.TaskID != task.ID || revision.Revision == 0 || seen[revision.Revision] {
			continue
		}
		seen[revision.Revision] = true
		scope := agentcontext.Scope{TaskID: task.ID, PlanRevision: int(revision.Revision)}
		identity := map[string]any{"source": "immutable_task_revision", "revision": revision.Revision,
			"base_revision": revision.BaseRevision, "current_revision": revision.Revision == task.CurrentRevision,
			"lifecycle_status": record.Status, "request": revision.Request, "intent": revision.Intent,
			"change_set": revision.ChangeSet, "steps": revision.Steps, "approval_required": revision.ApprovalRequired,
			"creator": revision.Creator, "idempotency_key": revision.IdempotencyKey}
		wire, _ := json.Marshal(identity)
		d.Records = append(d.Records, agentcontext.Record{ID: fmt.Sprintf("revision-source:%d", revision.Revision), Kind: "guard", Scope: scope,
			Statement: "不可变版本来源（历史请求及约束仅属于该版本，步骤定义状态不是当前物理事实）：" + string(wire)})
		if revision.Plan != nil {
			plan, _ := json.Marshal(revision.Plan)
			d.Records = append(d.Records, agentcontext.Record{ID: fmt.Sprintf("revision-plan:%d", revision.Revision), Kind: "system", Scope: scope,
				Statement: "该版本冻结计划原文：" + string(plan)})
		}
	}
	// A revision count is not evidence of coverage: revisions may contain gaps,
	// records belonging to other tasks, or proposed future versions.
	covered := uint64(0)
	for revision := range seen {
		if revision <= task.CurrentRevision {
			covered++
		}
	}
	complete := task.CurrentRevision > 0 && covered == task.CurrentRevision
	d.Records = append(d.Records, agentcontext.Record{ID: "revision-source:coverage", Kind: "guard", Scope: d.Scope,
		Statement: fmt.Sprintf("版本来源覆盖：当前版本=%d；已读取本任务1至当前版本中的%d个版本；完整=%t。未读取的旧请求和约束保持未知，不能从当前请求或旧模型摘要补写。", task.CurrentRevision, covered, complete)})
	if agentcontext.Mode() == "factorial" {
		d.DecisionMetadata = nil
		native := decisionMetadata(task, d)
		d.DecisionMetadata = &native
	}
	return d
}

func DecisionContextForRevisions(task Task, revisions []RevisionRecord, stage string, now time.Time) agentcontext.DecisionContext {
	d := ContextForRevisions(task, revisions, stage, now)
	if d.DecisionMetadata == nil {
		native := decisionMetadata(task, d)
		d.DecisionMetadata = &native
	}
	return agentcontext.DecisionFrom(d)
}
