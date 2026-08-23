// Package recovery classifies execution failures without exposing raw errors
// to end users or accidentally retrying an unknown physical side effect.
package recovery

import (
	"errors"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

type Class string

const (
	ObservationWait    Class = "OBSERVATION_WAIT"
	PolicyRetry        Class = "POLICY_RETRY"
	PolicyBlocked      Class = "POLICY_BLOCKED"
	ExecutionReconcile Class = "EXECUTION_RECONCILE"
	SafeRecovery       Class = "SAFE_RECOVERY"
	SafetyStop         Class = "SAFETY_STOP"
)

type Activity struct {
	Class            Class
	Retryable        bool
	TechnicalCode    string
	KnownState       string
	RobotSafetyState string
	AutomaticAction  string
}

func Classify(err error) Activity {
	switch {
	case errors.Is(err, policy.ErrObservationStale), errors.Is(err, policy.ErrObservationUnavailable):
		return Activity{
			Class: ObservationWait, Retryable: true, TechnicalCode: "POLICY_OBSERVATION_NOT_READY",
			KnownState:       "环境信息暂时不足，已经确认的任务进度仍然保留",
			RobotSafetyState: "机器人尚未收到新的运动指令",
			AutomaticAction:  "系统正在重新读取相机和机器人状态",
		}
	case errors.Is(err, policy.ErrProviderTimeout):
		return Activity{
			Class: PolicyRetry, Retryable: true, TechnicalCode: "POLICY_PROVIDER_TIMEOUT",
			KnownState:       "控制模型暂时没有返回动作，任务进度已经保留",
			RobotSafetyState: "机器人尚未收到新的运动指令",
			AutomaticAction:  "系统正在使用同一任务命令重新请求动作",
		}
	case errors.Is(err, policy.ErrProviderUnavailable):
		return Activity{
			Class: PolicyRetry, Retryable: true, TechnicalCode: "POLICY_PROVIDER_UNAVAILABLE",
			KnownState:       "控制模型暂时不可用，任务进度已经保留",
			RobotSafetyState: "机器人尚未收到新的运动指令",
			AutomaticAction:  "系统正在重新连接控制模型",
		}
	case errors.Is(err, policy.ErrManifestDrift), errors.Is(err, policy.ErrPolicyIncompatible),
		errors.Is(err, policy.ErrActionUnsafe), errors.Is(err, policy.ErrInferenceIdentity),
		errors.Is(err, policy.ErrArtifactIdentity):
		return Activity{
			Class: PolicyBlocked, TechnicalCode: "POLICY_COMPATIBILITY_OR_ACTION_REJECTED",
			KnownState:       "控制模型与当前机器人或任务环境不匹配",
			RobotSafetyState: "系统已拒绝动作，机器人没有执行这次控制输出",
			AutomaticAction:  "系统保持任务暂停，等待正确的模型、地图或标定配置",
		}
	case errors.Is(err, runtime.ErrSkillStreamClosed):
		return Activity{
			Class: ExecutionReconcile, TechnicalCode: "EXECUTION_OUTCOME_UNKNOWN",
			KnownState:       "机器人连接在动作结果返回前中断",
			RobotSafetyState: "系统不会自动重复可能已经执行的动作",
			AutomaticAction:  "系统正在通过环境观测确认机器人和物品的最后状态",
		}
	case errors.Is(err, runtime.ErrCapabilityUnavailable), errors.Is(err, runtime.ErrRobotNotReady):
		return Activity{
			Class: SafeRecovery, TechnicalCode: "RUNTIME_NOT_READY",
			KnownState:       "机器人当前不能继续这个动作",
			RobotSafetyState: "系统已经停止推进当前步骤",
			AutomaticAction:  "系统等待机器人恢复到可执行的安全状态",
		}
	default:
		return Activity{
			Class: SafeRecovery, TechnicalCode: "UNCLASSIFIED_EXECUTION_FAILURE",
			KnownState:       "当前步骤没有得到可靠的完成结果",
			RobotSafetyState: "系统不会把失败或未知结果当成完成",
			AutomaticAction:  "系统保留最后可信状态并停止推进当前步骤",
		}
	}
}
