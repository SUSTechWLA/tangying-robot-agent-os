package tasks

import (
	"strings"
	"testing"
)

func TestRecoveryGuidanceFromEventsProjectsSafeTimeline(t *testing.T) {
	events := []TaskEvent{{
		Type: "RECOVERY_ACTIVITY", StepID: "handoff/sender", Payload: map[string]any{
			"recoveryClass": "POLICY_RETRY", "attempt": uint64(1), "maxAttempts": uint64(3),
			"knownState":       "控制模型暂时没有返回动作，任务进度已经保留",
			"robotSafetyState": "机器人尚未收到新的运动指令",
			"automaticAction":  "系统正在使用同一任务命令重新请求动作",
			"technicalCode":    "POLICY_PROVIDER_TIMEOUT", "rawError": "bearerToken=secret",
		},
	}}
	guidance := RecoveryGuidanceFromEvents(events)
	if guidance == nil || len(guidance.Timeline) != 1 || guidance.Timeline[0].Class != "POLICY_RETRY" {
		t.Fatalf("guidance = %#v", guidance)
	}
	wire := guidance.KnownState + guidance.RobotSafetyState + guidance.AutomaticAction
	if strings.Contains(wire, "secret") || strings.Contains(wire, "bearerToken") {
		t.Fatalf("raw error leaked: %q", wire)
	}
}
