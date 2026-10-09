package recoveryexec

import (
	"context"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

// VerifyReadOnly independently repeats the last successful diagnostic read.
// It proves that the read is available, not that an underlying physical fault
// has disappeared. Writes require their own effect-specific verifier.
func VerifyReadOnly(registry Registry) Verify {
	return func(ctx context.Context, action agentruntime.RecoveryAction, outcome actionloop.Outcome) (Verdict, error) {
		if action.Risk != agentruntime.RiskReadOnly || outcome.Calls == 0 || !outcome.Completed || outcome.Escalated {
			return Verdict{Detail: "诊断读取未完成，或该动作需要专用物理复验"}, nil
		}
		for i := len(outcome.Rounds) - 1; i >= 0; i-- {
			round := outcome.Rounds[i]
			if round.Tool == "" || round.Tool == actionloop.ContextReadTool {
				// Paging an already stored result is not a fresh diagnostic read.
				// Re-verify the last actual action tool, never the local archive.
				continue
			}
			allowed := false
			for _, name := range action.Tools {
				allowed = allowed || name == round.Tool
			}
			tool, ok := registry.Lookup(round.Tool)
			if !allowed || !ok || tool.MutatesWorld || tool.SafetyLevel != skills.SafetyReadOnly || round.Verdict != actionloop.VerdictSatisfied {
				return Verdict{Detail: "诊断工具不满足只读复验契约"}, nil
			}
			result, err := tool.Call(ctx, round.Arguments)
			if err != nil {
				return Verdict{}, err
			}
			return Verdict{Verified: result.Success, Detail: fmt.Sprintf("独立重读 %s: success=%t；此结论只确认诊断读取，不宣称物理故障已修复", round.Tool, result.Success)}, nil
		}
		return Verdict{Detail: "没有成功诊断读取可复验"}, nil
	}
}
