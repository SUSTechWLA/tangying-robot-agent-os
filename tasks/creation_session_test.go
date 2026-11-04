package tasks_test

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type creationSessions struct {
	binding                        agentsession.Binding
	request, adapter, completedID  string
	completed, cleanupCanceled     bool
	cause, completionErr, beginErr error
	beginCalls                     int
}

func (s *creationSessions) Begin(ctx context.Context, request, adapter string) (context.Context, error) {
	s.beginCalls++
	s.request, s.adapter = request, adapter
	if s.beginErr != nil {
		return nil, s.beginErr
	}
	return agentsession.WithBinding(ctx, s.binding), nil
}
func (s *creationSessions) Complete(ctx context.Context, id string, cause error) error {
	s.completed, s.cleanupCanceled, s.completedID, s.cause = true, ctx.Err() != nil, id, cause
	return s.completionErr
}
func testCreationSessions() *creationSessions {
	text := "previous request was to navigate; it is not current authorization"
	return &creationSessions{binding: agentsession.Binding{SessionID: "session-a", TurnID: "turn-a", TaskID: "task-session-a", RobotID: "robot-a", Adapter: "gazebo", Sequence: 1, Context: text, ContextSHA256: agentcontext.Hash(text)}}
}

type creationGoal func(context.Context, string) (orchestration.Bundle, bool, error)

func (f creationGoal) PlanGoal(ctx context.Context, request string) (orchestration.Bundle, bool, error) {
	return f(ctx, request)
}
func creationBundle() orchestration.Bundle {
	return orchestration.Bundle{Capabilities: &capability.Plan{RobotID: "robot-a", CatalogRevision: "catalog-a", Calls: []capability.Call{{Tool: "navigation.status", Arguments: map[string]any{}}}}}
}

type creationLegacyPlanner func(string, manipulation.Intent, orchestration.World) (orchestration.Bundle, error)

func (f creationLegacyPlanner) Plan(request string, parsed manipulation.Intent, world orchestration.World) (orchestration.Bundle, error) {
	return f(request, parsed, world)
}

func TestCreationSessionBindingIsCommittedWithTaskAndPreservesRequest(t *testing.T) {
	for _, capabilityPlan := range []bool{false, true} {
		t.Run(map[bool]string{false: "legacy", true: "capability"}[capabilityPlan], func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "tasks.db")
			store, err := sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			service := tasks.NewService(store, intent.NewDeterministicParser())
			sessions := testCreationSessions()
			service.SetCreationSessions(sessions)
			request := "把红色杯子放进右侧收纳盒"
			if capabilityPlan {
				service.SetGoalPlanner(creationGoal(func(ctx context.Context, got string) (orchestration.Bundle, bool, error) {
					b, ok := agentsession.FromContext(ctx)
					if !ok || b.TurnID != "turn-a" || got != request {
						t.Fatal("original request or binding changed")
					}
					return creationBundle(), true, nil
				}))
			}
			task, err := service.Create(context.Background(), request, "gazebo")
			if err != nil {
				t.Fatal(err)
			}
			if task.ID != sessions.binding.TaskID || task.Request != request || task.Approved || !sessions.completed || sessions.completedID != task.ID || sessions.cause != nil {
				t.Fatalf("task=%+v session=%+v", task, sessions)
			}
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			store, err = sqlite.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			loaded, err := store.Get(context.Background(), task.ID)
			if err != nil {
				t.Fatal(err)
			}
			binding, ok := loaded.Events[0].Payload["session"].(map[string]any)
			if !ok || binding["taskId"] != task.ID || binding["turnId"] != "turn-a" || binding["contextSHA256"] != sessions.binding.ContextSHA256 {
				t.Fatalf("lost durable binding: %+v", loaded.Events)
			}
			revision, err := store.Revision(context.Background(), task.ID, 1)
			if err != nil || revision.Revision.Request != request || !revision.Revision.ApprovalRequired {
				t.Fatalf("revision changed: %+v %v", revision, err)
			}
		})
	}
}

func TestCreationSessionFailureCleanupSurvivesCanceledPlanning(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	sessions := testCreationSessions()
	service.SetCreationSessions(sessions)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	service.SetGoalPlanner(creationGoal(func(ctx context.Context, _ string) (orchestration.Bundle, bool, error) {
		cancel()
		return orchestration.Bundle{}, true, ctx.Err()
	}))
	if task, err := service.Create(ctx, "查询状态", "gazebo"); task != nil || !errors.Is(err, context.Canceled) {
		t.Fatalf("%+v %v", task, err)
	}
	if !sessions.completed || sessions.cleanupCanceled || sessions.completedID != "" || !errors.Is(sessions.cause, context.Canceled) {
		t.Fatalf("cleanup %+v", sessions)
	}
}

func TestCreatedTaskDoesNotBecomeRetryableErrorWhenSessionFinalizationFails(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	sessions := testCreationSessions()
	sessions.completionErr = errors.New("journal unavailable")
	service.SetCreationSessions(sessions)
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "gazebo")
	if err != nil || task == nil {
		t.Fatalf("committed task lost: %+v %v", task, err)
	}
	if _, err := store.Get(context.Background(), sessions.binding.TaskID); err != nil {
		t.Fatal(err)
	}
}

type sessionFailingRepository struct {
	tasks.Repository
	err error
}

func (s sessionFailingRepository) CreateWithRevision(context.Context, *tasks.Task, *tasks.TaskRevision) error {
	return s.err
}

func TestFailedTaskCommitIsNotRecordedAsSuccessfulSessionTurn(t *testing.T) {
	cause := errors.New("task journal unavailable")
	store := tasks.NewMemoryStore()
	service := tasks.NewService(sessionFailingRepository{Repository: store, err: cause}, intent.NewDeterministicParser())
	sessions := testCreationSessions()
	service.SetCreationSessions(sessions)
	if task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "gazebo"); task != nil || !errors.Is(err, cause) {
		t.Fatalf("failed commit returned a task: %+v %v", task, err)
	}
	if !sessions.completed || sessions.completedID != "" || !errors.Is(sessions.cause, cause) {
		t.Fatalf("failed commit became bound: %+v", sessions)
	}
	all, _ := store.List(context.Background())
	if len(all) != 0 {
		t.Fatal("failed task transaction leaked a task")
	}
}

func TestAutomaticSessionCannotResumeOrAuthorizeAmbiguousNewTask(t *testing.T) {
	for _, request := range []string{"继续", "继续刚才的任务", "retry", "再来一次。"} {
		t.Run(request, func(t *testing.T) {
			store := tasks.NewMemoryStore()
			service := tasks.NewService(store, intent.NewDeterministicParser())
			sessions := testCreationSessions()
			service.SetCreationSessions(sessions)
			service.SetGoalPlanner(creationGoal(func(context.Context, string) (orchestration.Bundle, bool, error) {
				t.Fatal("ambiguous request reached planner")
				return creationBundle(), true, nil
			}))
			if task, err := service.Create(context.Background(), request, "gazebo"); task != nil || !errors.Is(err, intent.ErrClarificationRequired) {
				t.Fatalf("ambiguous continuation accepted: %+v %v", task, err)
			}
			if sessions.completed || sessions.request != "" {
				t.Fatal("ambiguous continuation opened a new turn")
			}
			all, _ := store.List(context.Background())
			if len(all) != 0 {
				t.Fatal("refusal created a task")
			}
		})
	}
}

func TestAutomaticSessionRejectsInvalidInputBeforeBegin(t *testing.T) {
	for _, tc := range []struct {
		name, request, adapter string
	}{
		{"empty", "", "gazebo"},
		{"whitespace", " \n\t", "gazebo"},
		{"oversize_ascii", strings.Repeat("a", 8001), "gazebo"},
		{"oversize_utf8", strings.Repeat("中", 2667), "gazebo"},
		{"invalid_utf8", "查询" + string([]byte{0xff}), "gazebo"},
		{"oversize_adapter", "查询状态", strings.Repeat("a", 129)},
		{"invalid_adapter_utf8", "查询状态", string([]byte{0xff})},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := tasks.NewMemoryStore()
			service := tasks.NewService(store, intent.NewDeterministicParser())
			sessions := testCreationSessions()
			service.SetCreationSessions(sessions)
			service.SetGoalPlanner(creationGoal(func(context.Context, string) (orchestration.Bundle, bool, error) {
				t.Fatal("invalid input reached planner")
				return creationBundle(), true, nil
			}))
			if task, err := service.Create(context.Background(), tc.request, tc.adapter); task != nil || !errors.Is(err, intent.ErrClarificationRequired) {
				t.Fatalf("invalid input was not a clarification error: %+v %v", task, err)
			}
			if sessions.beginCalls != 0 || sessions.completed {
				t.Fatal("invalid input opened a session turn")
			}
			all, _ := store.List(context.Background())
			if len(all) != 0 {
				t.Fatal("invalid input created a task")
			}
		})
	}
}

func TestAutomaticSessionAcceptsExactByteLimitWithoutChangingRequest(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	sessions := testCreationSessions()
	service.SetCreationSessions(sessions)
	request := " " + strings.Repeat("中", 2666) + " " // 8000 bytes, including whitespace.
	service.SetGoalPlanner(creationGoal(func(_ context.Context, got string) (orchestration.Bundle, bool, error) {
		if got != request {
			t.Fatal("request was normalized or truncated")
		}
		return creationBundle(), true, nil
	}))
	task, err := service.Create(context.Background(), request, " GAZEBO ")
	if err != nil || task == nil || task.Request != request || sessions.beginCalls != 1 || sessions.request != request || sessions.adapter != "gazebo" {
		t.Fatalf("valid boundary input rejected or changed: %+v %+v %v", task, sessions, err)
	}
}

func TestAutomaticSessionBeginStorageFailureRemainsInfrastructureError(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	sessions := testCreationSessions()
	sessions.beginErr = errors.New("session storage unavailable")
	service.SetCreationSessions(sessions)
	service.SetGoalPlanner(creationGoal(func(context.Context, string) (orchestration.Bundle, bool, error) {
		t.Fatal("failed Begin reached planner")
		return creationBundle(), true, nil
	}))
	task, err := service.Create(context.Background(), "查询状态", "gazebo")
	if task != nil || !errors.Is(err, sessions.beginErr) || errors.Is(err, intent.ErrClarificationRequired) || sessions.beginCalls != 1 || sessions.completed {
		t.Fatalf("storage failure was misclassified: %+v %+v %v", task, sessions, err)
	}
}

func TestCreationWithoutSessionRetainsExistingInputPolicy(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	request := strings.Repeat("a", 8001)
	service.SetGoalPlanner(creationGoal(func(_ context.Context, got string) (orchestration.Bundle, bool, error) {
		if got != request {
			t.Fatal("request changed")
		}
		return creationBundle(), true, nil
	}))
	if task, err := service.Create(context.Background(), request, "gazebo"); err != nil || task == nil || task.Request != request {
		t.Fatalf("session input policy affected an unconfigured service: %+v %v", task, err)
	}
}

func TestAutomaticSessionRejectsInvalidBindingsAndForeignRobotPlan(t *testing.T) {
	for _, kind := range []string{"digest", "adapter", "foreign_robot"} {
		t.Run(kind, func(t *testing.T) {
			store := tasks.NewMemoryStore()
			service := tasks.NewService(store, intent.NewDeterministicParser())
			sessions := testCreationSessions()
			if kind == "digest" {
				sessions.binding.ContextSHA256 = "wrong"
			}
			if kind == "adapter" {
				sessions.binding.Adapter = "ros2"
			}
			service.SetCreationSessions(sessions)
			service.SetGoalPlanner(creationGoal(func(context.Context, string) (orchestration.Bundle, bool, error) {
				b := creationBundle()
				b.Capabilities.RobotID = "another-robot"
				return b, true, nil
			}))
			if task, err := service.Create(context.Background(), "查询状态", "gazebo"); task != nil || err == nil {
				t.Fatalf("invalid scope accepted: %+v %v", task, err)
			}
			all, _ := store.List(context.Background())
			if len(all) != 0 || !sessions.completed {
				t.Fatal("bad binding persisted or leaked gate")
			}
		})
	}
}

func TestAutomaticSessionScopesLegacyIntentBeforePlanning(t *testing.T) {
	for _, tc := range []struct {
		name, request string
		foreign       bool
	}{
		{"foreign_root", "让二号机器人把红色杯子放进右侧收纳盒", true},
		{"foreign_sequence", "让一号机器人把红色方块放到交接区，然后让二号机器人把红色方块从交接区放到右侧目标区", true},
		{"same_root", "让一号机器人把红色杯子放进右侧收纳盒", false},
		{"same_sequence", "让一号机器人把红色杯子放进右侧收纳盒，然后让一号机器人把蓝色杯子放进左侧收纳盒", false},
		{"implicit_root", "把红色杯子放进右侧收纳盒", false},
		{"implicit_sequence", "把红色杯子放进右侧收纳盒，然后把蓝色杯子放进左侧收纳盒", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := tasks.NewMemoryStore()
			service := tasks.NewService(store, intent.NewDeterministicParser())
			sessions := testCreationSessions()
			sessions.binding.RobotID = "robot-1"
			service.SetCreationSessions(sessions)
			service.SetGoalPlanner(creationGoal(func(context.Context, string) (orchestration.Bundle, bool, error) {
				return orchestration.Bundle{}, false, nil // Exercise the legacy fallback.
			}))
			planned := false
			service.SetPlanner(creationLegacyPlanner(func(_ string, _ manipulation.Intent, _ orchestration.World) (orchestration.Bundle, error) {
				planned = true
				return orchestration.Bundle{Source: orchestration.SourceDeterministic}, nil
			}))
			task, err := service.Create(context.Background(), tc.request, "gazebo")
			all, _ := store.List(context.Background())
			if tc.foreign {
				if task != nil || !errors.Is(err, intent.ErrClarificationRequired) || planned || len(all) != 0 {
					t.Fatalf("foreign robot entered planning or persistence: task=%+v err=%v planned=%v tasks=%d", task, err, planned, len(all))
				}
				if !sessions.completed || sessions.completedID != "" || !errors.Is(sessions.cause, intent.ErrClarificationRequired) {
					t.Fatalf("rejected intent was recorded as successful: %+v", sessions)
				}
				return
			}
			if err != nil || task == nil || !planned || len(all) != 1 || !sessions.completed || sessions.completedID != task.ID {
				t.Fatalf("local intent rejected: task=%+v err=%v planned=%v session=%+v", task, err, planned, sessions)
			}
			if strings.HasPrefix(tc.name, "implicit_") {
				if task.Intent.RobotID != "" {
					t.Fatal("implicit robot was rewritten")
				}
				for _, child := range task.Intent.Sequence {
					if child.RobotID != "" {
						t.Fatal("implicit sequence robot was rewritten")
					}
				}
			}
		})
	}
}

type creationFixedParser struct{ parsed manipulation.Intent }

func (p creationFixedParser) Parse(string) (manipulation.Intent, error) { return p.parsed, nil }

func TestAutomaticSessionScopesNestedLegacySequence(t *testing.T) {
	store := tasks.NewMemoryStore()
	parser := creationFixedParser{parsed: manipulation.Intent{RobotID: "robot-a", Sequence: []manipulation.Intent{{Sequence: []manipulation.Intent{{RobotID: "robot-b"}}}}}}
	service := tasks.NewService(store, parser, creationLegacyPlanner(func(string, manipulation.Intent, orchestration.World) (orchestration.Bundle, error) {
		t.Fatal("nested foreign robot reached planner")
		return orchestration.Bundle{}, nil
	}))
	sessions := testCreationSessions()
	service.SetCreationSessions(sessions)
	task, err := service.Create(context.Background(), "执行组合任务", "gazebo")
	all, _ := store.List(context.Background())
	if task != nil || !errors.Is(err, intent.ErrClarificationRequired) || len(all) != 0 || !sessions.completed || sessions.completedID != "" {
		t.Fatalf("nested foreign intent accepted: task=%+v err=%v session=%+v", task, err, sessions)
	}
}
