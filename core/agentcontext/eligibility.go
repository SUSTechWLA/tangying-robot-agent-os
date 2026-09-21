package agentcontext

import (
	"fmt"
	"sort"
	"strings"
)

// Eligibility checks identity/time metadata, never the truth of Statement. It is
// an explicit preprocessing intervention, separately evaluated from formatting.
func Eligibility(d Document) map[string][]string {
	reasons := map[string][]string{}
	for _, r := range d.Records {
		var why []string
		if d.Scope.TaskID == "" || d.Scope.RobotID == "" || d.Scope.PlanRevision == 0 || r.Scope.TaskID == "" || r.Scope.RobotID == "" || r.Scope.PlanRevision == 0 {
			why = append(why, "作用域字段缺失")
		}
		if r.Scope.TaskID != d.Scope.TaskID {
			why = append(why, "其他任务")
		}
		if r.Scope.RobotID != d.Scope.RobotID {
			why = append(why, "其他机器人")
		}
		if r.Scope.PlanRevision != d.Scope.PlanRevision {
			why = append(why, "其他计划版本")
		}
		if d.AsOfMS == 0 || r.ObservedMS == 0 || r.ValidUntilMS == 0 {
			why = append(why, "时间或有效期未知")
		}
		if r.ObservedMS > d.AsOfMS {
			why = append(why, "来自未来")
		}
		if r.ValidUntilMS > 0 && r.ValidUntilMS <= d.AsOfMS {
			why = append(why, "已经过期")
		}
		reasons[r.ID] = why
	}
	// Evaluate supersession from the base validity snapshot, so record order has
	// no effect and an expired/mismatched record cannot invalidate a current one.
	base := map[string]bool{}
	byID := map[string]Record{}
	for _, r := range d.Records {
		base[r.ID] = len(reasons[r.ID]) == 0
		byID[r.ID] = r
	}
	for _, r := range d.Records {
		if !base[r.ID] || (r.Kind != "verification" && r.Kind != "system" && r.Kind != "guard") {
			continue
		}
		for _, id := range r.Supersedes {
			old, ok := byID[id]
			if ok && r.Kind == old.Kind && r.Scope == old.Scope && r.ObservedMS >= old.ObservedMS && id != r.ID {
				reasons[id] = append(reasons[id], "被记录 "+r.ID+" 取代")
			}
		}
	}
	return reasons
}

func renderAnnotated(d Document) string {
	why := Eligibility(d)
	var b strings.Builder
	b.WriteString("【确定性元数据核对；不判断陈述真假、不授予执行权】\n")
	ordered := append([]Record(nil), d.Records...)
	sort.SliceStable(ordered, func(i, j int) bool {
		vi, vj := len(why[ordered[i].ID]) == 0, len(why[ordered[j].ID]) == 0
		if vi != vj {
			return vi
		}
		return ordered[i].ObservedMS > ordered[j].ObservedMS
	})
	for _, r := range ordered {
		status := "范围和时间匹配；内容类型仍为 " + r.Kind
		if len(why[r.ID]) > 0 {
			status = "不适用于当前决策：" + strings.Join(why[r.ID], "、")
		}
		fmt.Fprintf(&b, "记录 %s：%s\n", quoted(r.ID), status)
	}
	d.Records = ordered
	b.WriteString(renderHybrid(d))
	return b.String()
}
