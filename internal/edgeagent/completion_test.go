package edgeagent

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type completionReadFault struct {
	*missionRobot
	verificationReads int
	reads             int
	failures          int
	code              codes.Code
	withhold          bool
	cancel            context.CancelFunc
}

func (r *completionReadFault) CallService(ctx context.Context, q *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if q.Name == "mission.status" {
		r.reads++
		if r.reads > r.verificationReads && r.failures > 0 {
			r.failures--
			return nil, status.Error(r.code, "injected completion read fault")
		}
		if r.reads > r.verificationReads && r.withhold {
			if r.cancel != nil {
				r.cancel()
			}
			<-ctx.Done()
			return nil, ctx.Err()
		}
	}
	return r.missionRobot.CallService(ctx, q)
}

func TestDelegatedCompletionReadRecoversWithoutReplanningOrMovementReplay(t *testing.T) {
	r := &missionRobot{}
	c, d := objectiveFixture(t, r, 11)
	host, task, store := hostFixture(t, r, c, d)
	fault := &completionReadFault{missionRobot: r, verificationReads: 11, failures: 1, code: codes.Unavailable}
	host.Provider = fault
	if _, err := host.Run(context.Background(), task, nil, nil); err != nil {
		t.Fatalf("transient completion read stopped a verified mission: %v", err)
	}
	if len(r.writes) != 11 || d.rounds != 1 || fault.reads != 23 {
		t.Fatalf("completion retry repeated actions: writes=%v plans=%d reads=%d", r.writes, d.rounds, fault.reads)
	}
	final, _ := host.Tasks.Get(context.Background(), task.ID)
	finding, decision := false, false
	for _, event := range final.Events {
		finding = finding || event.Type == "EDGE_OPS_FINDING" && event.Payload["phase"] == "completion"
		decision = decision || event.Type == "EDGE_RECOVERY_DECISION" && event.Payload["decision"] == "RETRY_COMPLETION_READ" && event.Payload["diagnosedBy"] == "ops"
	}
	if !finding || !decision {
		t.Fatal("completion retry lacks persisted diagnosis and recovery")
	}
	s, _, _ := store.LoadState(context.Background(), goalKey(task))
	var cp checkpoint
	_ = json.Unmarshal(s.Data, &cp)
	if !cp.Complete || cp.Pending || cp.Reads != 23 {
		t.Fatalf("completion retry accounting differs: %+v", cp)
	}
	trace, _ := json.Marshal(map[string]any{"fault": "one_completion_read_unavailable", "writes": r.writes, "checkpoint": cp, "events": final.Events})
	t.Logf("trace=%s", trace)
}

func TestDelegatedCompletionReadFailureIsBoundedAcrossRestart(t *testing.T) {
	for _, mode := range []string{"retry-quota", "read-budget", "permission"} {
		t.Run(mode, func(t *testing.T) {
			r := &missionRobot{}
			c, d := objectiveFixture(t, r, 1)
			if mode == "read-budget" {
				c.Budget.MaxReads = 2
			}
			host, task, store := hostFixture(t, r, c, d)
			code := codes.Unavailable
			if mode == "permission" {
				code = codes.PermissionDenied
			}
			fault := &completionReadFault{missionRobot: r, verificationReads: 1, failures: 100, code: code}
			host.Provider = fault
			_, err := host.Run(context.Background(), task, nil, nil)
			if mode != "permission" && !errors.Is(err, capabilityagent.ErrNeedsInput) {
				t.Fatalf("exhausted completion budget lost typed stop: %v", err)
			}
			if mode == "permission" && status.Code(err) != codes.PermissionDenied {
				t.Fatalf("permission denial swallowed: %v", err)
			}
			firstReads := fault.reads
			if mode == "retry-quota" && firstReads != 4 || mode == "read-budget" && firstReads != 2 || mode == "permission" && firstReads != 2 {
				t.Fatalf("unexpected completion attempts=%d", firstReads-1)
			}
			if mode == "retry-quota" || mode == "read-budget" {
				restarted := &Host{Planner: host.Planner, Executor: host.Executor}
				_, err = restarted.Run(context.Background(), task, nil, nil)
				if !errors.Is(err, capabilityagent.ErrNeedsInput) || fault.reads != firstReads {
					t.Fatalf("restart reset retry budget: first=%d now=%d error=%v", firstReads, fault.reads, err)
				}
			}
			if len(r.writes) != 1 || d.rounds != 1 {
				t.Fatalf("completion failure repeated effects: writes=%v plans=%d", r.writes, d.rounds)
			}
			s, _, _ := store.LoadState(context.Background(), goalKey(task))
			t.Logf("mode=%s writes=1 plans=1 completion_reads=%d checkpoint=%s error=%v", mode, fault.reads-1, s.Data, err)
		})
	}
}

func TestDelegatedCompletionDoesNotRetryCancellationOrDeadline(t *testing.T) {
	for _, mode := range []string{"deadline", "cancelled"} {
		t.Run(mode, func(t *testing.T) {
			r := &missionRobot{}
			c, d := objectiveFixture(t, r, 1)
			c.Budget.TimeoutSeconds = 1
			host, task, _ := hostFixture(t, r, c, d)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			fault := &completionReadFault{missionRobot: r, verificationReads: 1, withhold: true}
			want := context.DeadlineExceeded
			if mode == "cancelled" {
				fault.cancel = cancel
				want = context.Canceled
			}
			host.Provider = fault
			_, err := host.Run(ctx, task, nil, nil)
			if !errors.Is(err, want) || fault.reads != 2 || len(r.writes) != 1 || d.rounds != 1 {
				t.Fatalf("cancellation/deadline was retried or hidden: %v reads=%d writes=%v", err, fault.reads, r.writes)
			}
			if _, err := host.Run(ctx, task, nil, nil); !errors.Is(err, want) || fault.reads != 2 {
				t.Fatalf("restart ignored cancellation/deadline: %v reads=%d", err, fault.reads)
			}
			if strings.Contains(err.Error(), "retry") {
				t.Fatal("expired objective requested completion recovery")
			}
		})
	}
}

type completionExhaustionCommitFault struct {
	*sqlite.Store
	failures int
	kind     string
}

func (s *completionExhaustionCommitFault) Commit(ctx context.Context, request eventstore.CommitRequest) error {
	kind := s.kind
	if kind == "" {
		kind = "EDGE_COMPLETION_RECOVERY_EXHAUSTED"
	}
	for _, event := range request.Events {
		if event.EventType == kind && s.failures == 0 {
			s.failures++
			return errors.New("injected completion exhaustion commit failure")
		}
	}
	return s.Store.Commit(ctx, request)
}

func TestDelegatedInterruptedCompletionAttemptRequiresReviewWithoutAnotherRead(t *testing.T) {
	for _, kind := range []string{"EDGE_COMPLETION_READ_CONFIRMED", "EDGE_COMPLETION_RETRY_RESERVED"} {
		t.Run(kind, func(t *testing.T) {
			r := &missionRobot{}
			c, d := objectiveFixture(t, r, 1)
			host, task, store := hostFixture(t, r, c, d)
			fault := &completionReadFault{missionRobot: r, verificationReads: 1, code: codes.Unavailable}
			if kind == "EDGE_COMPLETION_RETRY_RESERVED" {
				fault.failures = 1
			}
			host.Provider = fault
			host.Store = &completionExhaustionCommitFault{Store: store, kind: kind}
			if _, err := host.Run(context.Background(), task, nil, nil); err == nil || !strings.Contains(err.Error(), "injected completion exhaustion commit failure") {
				t.Fatalf("completion acknowledgement fault missed: %v", err)
			}
			if err := host.CheckRecovery(context.Background(), task); err != nil {
				t.Fatalf("a pending read was mislabeled as unknown motion: %v", err)
			}
			firstReads := fault.reads
			if _, err := host.Run(context.Background(), task, nil, nil); !errors.Is(err, capabilityagent.ErrNeedsInput) || fault.reads != firstReads || len(r.writes) != 1 || d.rounds != 1 {
				t.Fatalf("interrupted completion attempt replayed: error=%v reads=%d writes=%v plans=%d", err, fault.reads, r.writes, d.rounds)
			}
			s, _, _ := store.LoadState(context.Background(), goalKey(task))
			t.Logf("fault_commit=%s confirmed_writes=1 completion_reads=1 restart_extra_reads=0 checkpoint=%s", kind, s.Data)
		})
	}
}

func TestDelegatedCompletionExhaustionCommitFailureCannotGrantAnotherRead(t *testing.T) {
	r := &missionRobot{}
	c, d := objectiveFixture(t, r, 1)
	host, task, store := hostFixture(t, r, c, d)
	fault := &completionReadFault{missionRobot: r, verificationReads: 1, failures: 100, code: codes.Unavailable}
	host.Provider = fault
	host.Store = &completionExhaustionCommitFault{Store: store}
	_, err := host.Run(context.Background(), task, nil, nil)
	if err == nil || !strings.Contains(err.Error(), "injected completion exhaustion commit failure") || fault.reads != 4 {
		t.Fatalf("exhaustion commit fault missed: error=%v reads=%d", err, fault.reads)
	}
	s, _, _ := store.LoadState(context.Background(), goalKey(task))
	t.Logf("before_restart_checkpoint=%s", s.Data)
	restarted := &Host{Planner: host.Planner, Executor: host.Executor}
	_, err = restarted.Run(context.Background(), task, nil, nil)
	if !errors.Is(err, capabilityagent.ErrNeedsInput) || fault.reads != 4 || len(r.writes) != 1 || d.rounds != 1 {
		t.Fatalf("failed exhaustion acknowledgement granted extra read: error=%v reads=%d writes=%v plans=%d", err, fault.reads, r.writes, d.rounds)
	}
	t.Logf("completion_attempts=3 retries=2 writes=1 plans=1 restart_extra_reads=0 error=%v", err)
}
