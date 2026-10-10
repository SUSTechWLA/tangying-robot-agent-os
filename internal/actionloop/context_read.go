package actionloop

import (
	"context"
	"encoding/json"
	"fmt"
	"math"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
)

const ContextReadTool = agentcontext.ArchiveReadTool

// ContextArchiveTool exposes the same scoped, read-only archive reader to
// bounded planning loops that do not execute through Loop.Run.
func ContextArchiveTool(snapshot ContextSnapshot) Tool { return contextReadTool(snapshot) }

func contextReadTool(snapshot ContextSnapshot) Tool {
	return Tool{
		Name: ContextReadTool, SafetyLevel: skills.SafetyReadOnly,
		Description: "读取当前上下文完整历史归档；只读，不执行机器人动作。sha256 来自归档索引；可用 item_id 精确读记录/轮次。content 是明确分页的原 JSON，next_offset 用于续读，不能把不完整页面当完整证据。",
		InputSchema: map[string]any{"type": "object", "properties": map[string]any{
			"sha256": map[string]any{"type": "string"}, "item_id": map[string]any{"type": "string"},
			"offset": map[string]any{"type": "integer", "minimum": 0}, "limit": map[string]any{"type": "integer", "minimum": 1, "maximum": 4096},
		}, "required": []string{"sha256"}, "additionalProperties": false},
		Call: func(_ context.Context, args map[string]any) (Result, error) {
			fail := func(err error) (Result, error) {
				return Result{Code: "INVALID_ARGUMENT", Message: "context_read: " + err.Error()}, nil
			}
			for key := range args {
				if key != "sha256" && key != "item_id" && key != "offset" && key != "limit" {
					return fail(fmt.Errorf("unknown context read argument %s", key))
				}
			}
			sha, ok := args["sha256"].(string)
			if !ok || sha == "" {
				return fail(fmt.Errorf("sha256 required"))
			}
			item := ""
			if v, exists := args["item_id"]; exists {
				var ok bool
				item, ok = v.(string)
				if !ok {
					return fail(fmt.Errorf("invalid item_id"))
				}
			}
			number := func(key string, defaultValue int) (int, error) {
				v, exists := args[key]
				if !exists {
					return defaultValue, nil
				}
				switch n := v.(type) {
				case int:
					return n, nil
				case float64:
					if !math.IsNaN(n) && !math.IsInf(n, 0) && n >= 0 && n <= float64(1<<30) && n == math.Trunc(n) {
						return int(n), nil
					}
				}
				return 0, fmt.Errorf("invalid %s", key)
			}
			offset, err := number("offset", 0)
			if err != nil {
				return fail(err)
			}
			limit, err := number("limit", 4096)
			if err != nil {
				return fail(err)
			}
			if limit < 1 || limit > 4096 {
				return fail(fmt.Errorf("invalid limit"))
			}
			if snapshot.Compaction != nil {
				pageCap := snapshot.Compaction.Budget.MaxContextBytes / 8
				if pageCap < 1 {
					pageCap = 1
				}
				if limit > pageCap {
					limit = pageCap
				}
			}
			// Scope is captured from the exact source; the model cannot supply or
			// widen it. Only captured immutable snapshots in this loop/scope exist.
			page, err := snapshot.ReadArtifact(snapshot.Scope, sha, item, offset, limit)
			if err != nil {
				return fail(err)
			}
			data, err := json.Marshal(page)
			if err != nil {
				return fail(err)
			}
			var detail map[string]any
			if err := json.Unmarshal(data, &detail); err != nil {
				return fail(err)
			}
			return Result{Success: true, Message: "历史归档读取；时间和权限沿用原记录。", Detail: detail}, nil
		},
	}
}
