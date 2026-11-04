package tasks

import (
	"context"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// CreationSessions is an optional local intake journal, separate from task
// execution and approval. Begin reserves an identity; Complete never executes
// or resumes a task. Implementations must release their intake gate on Complete.
type CreationSessions interface {
	Begin(context.Context, string, string) (context.Context, error)
	Complete(context.Context, string, error) error
}

// SetCreationSessions configures automatic sessions for future local requests.
// An in-flight creation retains its original coordinator.
func (s *Service) SetCreationSessions(sessions CreationSessions) {
	s.plannerMu.Lock()
	defer s.plannerMu.Unlock()
	s.sessions = sessions
}

func (s *Service) Create(ctx context.Context, request, adapter string) (task *Task, err error) {
	s.plannerMu.RLock()
	sessions := s.sessions
	s.plannerMu.RUnlock()
	if sessions == nil {
		return s.create(ctx, request, adapter)
	}
	// Reject user input before a turn is reserved. Begin also reports storage
	// failures, so its errors must not all be classified as clarification.
	if strings.TrimSpace(request) == "" || len(request) > 8000 || !utf8.ValidString(request) {
		return nil, fmt.Errorf("%w: 任务请求须为非空 UTF-8 文本，且不超过 8000 字节", intent.ErrClarificationRequired)
	}
	normalizedAdapter := NormalizeAdapter(adapter)
	if len(normalizedAdapter) > 128 || !utf8.ValidString(adapter) {
		return nil, fmt.Errorf("%w: 机器人适配器须为有效 UTF-8 文本，规范化后不超过 128 字节", intent.ErrClarificationRequired)
	}
	// Session continuity must not turn an ambiguous follow-up into a new
	// physical task, particularly when an earlier task has UNKNOWN outcomes.
	// The explicit resume/reconcile endpoints retain that authority.
	if continuationOnly(request) {
		return nil, fmt.Errorf("%w: 请明确新的任务目标；继续已有任务请使用任务记录中的继续或对账入口，历史会话不能授权重复动作", intent.ErrClarificationRequired)
	}
	ctx, err = sessions.Begin(ctx, request, normalizedAdapter)
	if err != nil {
		return nil, err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		id := ""
		if task != nil {
			id = task.ID
		}
		// The task and its session binding were already atomically committed.
		// Returning an error now invites duplicate task creation after a retry.
		// A failed session commit retains the reservation so the next Begin can
		// reconcile its preallocated task ID, never replay the physical work.
		_ = sessions.Complete(cleanup, id, err)
	}()
	b, ok := agentsession.FromContext(ctx)
	if !ok || b.Validate() != nil || b.Adapter != NormalizeAdapter(adapter) {
		return nil, fmt.Errorf("invalid automatic session binding")
	}
	return s.create(ctx, request, adapter)
}

func continuationOnly(request string) bool {
	value := strings.ToLower(strings.Trim(strings.TrimSpace(request), "。.!！?？"))
	switch value {
	case "继续", "继续吧", "继续任务", "继续刚才的任务", "继续上一个任务", "接着做", "再来一次", "重复刚才的任务", "重试", "continue", "resume", "retry", "do it again":
		return true
	default:
		return false
	}
}

func creationTaskID(ctx context.Context) string {
	if b, ok := agentsession.FromContext(ctx); ok {
		return b.TaskID
	}
	return newID("task")
}

func creationPayload(ctx context.Context, payload map[string]any) map[string]any {
	if b, ok := agentsession.FromContext(ctx); ok {
		if payload == nil {
			payload = map[string]any{}
		}
		payload["session"] = b
	}
	return payload
}

func validateCreationTask(ctx context.Context, id, adapter, robot string) error {
	if b, ok := agentsession.FromContext(ctx); ok && (b.Validate() != nil || robot != b.RobotID || id != b.TaskID || NormalizeAdapter(adapter) != b.Adapter) {
		return fmt.Errorf("automatic session binding differs from capability task")
	}
	return nil
}

// An omitted robot keeps the existing local execution semantics. An explicit
// robot, including one nested in a composite intent, must belong to this session
// before the legacy planner or repository can consume the intent.
func validateCreationIntent(ctx context.Context, parsed manipulation.Intent) error {
	binding, ok := agentsession.FromContext(ctx)
	if !ok {
		return nil
	}
	var check func(manipulation.Intent) error
	check = func(current manipulation.Intent) error {
		if current.RobotID != "" && current.RobotID != binding.RobotID {
			return fmt.Errorf("%w: 当前会话属于机器人 %q，任务指定了机器人 %q；请切换目标机器人后重新提交", intent.ErrClarificationRequired, binding.RobotID, current.RobotID)
		}
		for _, child := range current.Sequence {
			if err := check(child); err != nil {
				return err
			}
		}
		return nil
	}
	return check(parsed)
}
