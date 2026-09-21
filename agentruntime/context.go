package agentruntime

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

func FindingContext(f Finding, taskID string, now time.Time) agentcontext.Document {
	facts, _ := json.Marshal(f.Facts)
	return agentcontext.Document{SchemaVersion: agentcontext.Version, Stage: "ops", Role: "ops", Goal: "定位异常并保留可复核的证据链", Scope: agentcontext.Scope{TaskID: taskID}, AsOfMS: now.UnixMilli(), Summary: f.Message,
		Records:     []agentcontext.Record{{ID: "ops:finding", Kind: "system", Scope: agentcontext.Scope{TaskID: taskID}, Statement: fmt.Sprintf("规则发现=%s；组件=%s；分类=%s；严重度=%s；消息=%s；规则输入=%s", f.Code, f.Component, f.Category, f.Severity, f.Message, facts), EvidenceIDs: f.Evidence}},
		Constraints: []string{fmt.Sprintf("自动重试禁止=%t；该发现不授予执行权限。", f.AutomaticRetryForbidden), "规则推断不能升级为新的物理观测；缺失时间和作用域必须保留未知。"}, Questions: append([]string(nil), f.MissingEvidence...)}
}

// ContextDocument makes the planner seam usable by future model/optimization
// agents while retaining the existing deterministic permission boundary.
func (r RecoveryRequest) ContextDocument(now time.Time) agentcontext.Document {
	d := agentcontext.Document{SchemaVersion: agentcontext.Version, Stage: "recovery", Role: "recovery", Goal: "调查并提出恢复方案：" + r.Diagnosis, Scope: agentcontext.Scope{TaskID: r.Facts.TaskID}, AsOfMS: now.UnixMilli(), Summary: r.Diagnosis,
		Constraints: []string{"目录是唯一可建议的动作范围；建议不是批准，执行器单独检查。", "执行终态未知或账本不可读时，不得派发新的物理动作。", "模型输出属于假设，不能覆盖观测和验证记录。"}}
	facts, _ := json.Marshal(r.Facts)
	d.Records = append(d.Records, agentcontext.Record{ID: "recovery:facts", Kind: "system", Scope: d.Scope, Statement: "调查读取结果（采集时刻未统一声明）：" + string(facts)})
	for _, a := range r.Catalog {
		d.Tools = append(d.Tools, agentcontext.Tool{Name: a.ID, Description: a.Summary + "；风险=" + string(a.Risk), MutatesWorld: a.Risk != RiskReadOnly, RequiresApproval: a.RequiresApproval()})
	}
	if r.Trail != nil {
		for _, s := range r.Trail.Steps {
			data, _ := json.Marshal(s)
			kind := "system"
			if s.Kind == TrailModel {
				kind = "hypothesis"
			}
			d.Records = append(d.Records, agentcontext.Record{ID: fmt.Sprintf("trail:%s:%d", r.Trail.ID, s.Sequence), Kind: kind, Scope: d.Scope, Statement: string(data)})
		}
	}
	return d
}

func contextEnvelope(d agentcontext.Document) map[string]any {
	mode := agentcontext.Mode()
	if mode == "legacy" {
		return nil
	}
	projection, err := agentcontext.Project(d, d.Stage)
	if err != nil {
		return map[string]any{"error": err.Error()}
	}
	data, _ := json.Marshal(projection)
	var envelope map[string]any
	_ = json.Unmarshal(data, &envelope)
	return envelope
}
