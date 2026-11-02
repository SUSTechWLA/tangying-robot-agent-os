package capabilityagent

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type traceCampaignProvider struct {
	*fakeProvider
	mode  string
	reads int
}

func (p *traceCampaignProvider) CallService(ctx context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if r.Name == "calibration.get" {
		p.reads++
		if p.mode == "read-retry" && p.reads == 1 {
			return nil, status.Error(codes.Unavailable, "injected first read failure")
		}
	}
	if r.Name == "mapping.build" && p.mode == "lost-write-reply" {
		p.starts++
		return nil, status.Error(codes.Unavailable, "injected reply loss after admission")
	}
	return p.fakeProvider.CallService(ctx, r)
}

// Deterministic software contract campaign, not Gazebo/model/hardware evidence.
// Real session, planning, approval, execution and persistence; injected provider.
func TestTraceNaturalLanguageCampaign(t *testing.T) {
	ctx := context.Background()
	for repeat := 1; repeat <= 3; repeat++ {
		for _, mode := range []string{"normal", "read-retry", "bad-verification", "lost-write-reply"} {
			t.Run(fmt.Sprintf("%s-%d", mode, repeat), func(t *testing.T) {
				path := filepath.Join(t.TempDir(), "campaign.db")
				store, err := sqlite.Open(path)
				if err != nil {
					t.Fatal(err)
				}
				defer func() { _ = store.Close() }()
				provider := &traceCampaignProvider{fakeProvider: &fakeProvider{badEvidence: mode == "bad-verification"}, mode: mode}
				service := tasks.NewService(store, intent.NewDeterministicParser())
				service.SetGoalPlanner(&Planner{Provider: provider})
				sessions, err := agentsession.New(store, store, "heterogeneous-unit")
				if err != nil {
					t.Fatal(err)
				}
				service.SetCreationSessions(sessions)
				const request = "检查标定，建图"
				task, err := service.Create(ctx, request, "gazebo")
				if err != nil {
					t.Fatal(err)
				}
				task, err = service.Approve(ctx, task.ID, "software-campaign")
				if err != nil {
					t.Fatal(err)
				}
				for _, state := range []taskgraph.TaskState{taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting} {
					if err = service.Transition(ctx, task.ID, state, "software campaign"); err != nil {
						t.Fatal(err)
					}
				}
				diagnoses := 0
				executor := &Executor{Provider: provider, Tasks: service, Store: store, PollInterval: time.Millisecond, RecoverRead: func(context.Context, string, string, string, error) error { diagnoses++; return nil }}
				completed, runErr := executor.Run(ctx, task, nil, nil)
				wantSuccess := mode == "normal" || mode == "read-retry"
				if (runErr == nil) != wantSuccess {
					t.Fatalf("mode=%s outcome=%v", mode, runErr)
				}
				if wantSuccess {
					for _, state := range []taskgraph.TaskState{taskgraph.StateVerifying, taskgraph.StateSucceeded} {
						if err = service.Transition(ctx, task.ID, state, "software verifier completed"); err != nil {
							t.Fatal(err)
						}
					}
				} else if err = service.Transition(ctx, task.ID, taskgraph.StateRecoverableFailure, "software verifier refused completion"); err != nil {
					t.Fatal(err)
				}
				trace, err := service.Trace(ctx, task.ID, store)
				if err != nil {
					t.Fatal(err)
				}
				if trace.OriginalRequest.Text != request || trace.Session == nil || len(trace.RPCCalls) == 0 || !trace.Source.Stable {
					t.Fatalf("broken origin: %+v", trace)
				}
				for _, finding := range trace.Integrity.Findings {
					if finding.Severity == "error" {
						t.Fatalf("unexpected inconsistent evidence: %+v", finding)
					}
				}
				failedRPC := 0
				seen := map[string]bool{}
				for _, rpc := range trace.RPCCalls {
					if rpc.RPCID == "" || seen[rpc.RPCID] || len(rpc.EventSequences) != 2 || rpc.CommandID == "" || rpc.TaskRevision != 1 || rpc.RobotID != "heterogeneous-unit" {
						t.Fatalf("unbound atomic attempt: %+v", rpc)
					}
					seen[rpc.RPCID] = true
					if rpc.Status == "FAILED" {
						failedRPC++
					}
				}
				if mode == "read-retry" && (diagnoses != 1 || provider.reads != 2 || failedRPC != 1) {
					t.Fatalf("retry evidence diagnoses=%d reads=%d failedRPC=%d", diagnoses, provider.reads, failedRPC)
				}
				if mode == "bad-verification" && failedRPC != 0 {
					t.Fatal("successful transport confused with rejected postcondition")
				}
				if mode == "lost-write-reply" && (provider.starts != 1 || failedRPC != 1 || diagnoses != 0) {
					t.Fatal("unknown write replayed or hidden")
				}
				before, _ := json.Marshal(trace)
				if err = store.Close(); err != nil {
					t.Fatal(err)
				}
				store, err = sqlite.Open(path)
				if err != nil {
					t.Fatal(err)
				}
				after, err := tasks.NewService(store, nil).Trace(ctx, task.ID, store)
				if err != nil {
					t.Fatal(err)
				}
				wire, _ := json.Marshal(after)
				if string(before) != string(wire) {
					t.Fatal("durable trace changed after restart")
				}
				if directory := os.Getenv("TRACE_CAMPAIGN_OUTPUT"); directory != "" {
					if err = os.MkdirAll(directory, 0755); err != nil {
						t.Fatal(err)
					}
					artifact := map[string]any{"evidenceClass": "software-contract-fixture", "scenario": mode, "repeat": repeat, "completedSteps": completed, "diagnoses": diagnoses, "physicalStarts": provider.starts, "failedRPCs": failedRPC, "reopenIdentical": true, "trace": trace}
					if runErr != nil {
						artifact["executionError"] = runErr.Error()
					}
					output, _ := json.MarshalIndent(artifact, "", "  ")
					if err = os.WriteFile(filepath.Join(directory, fmt.Sprintf("%s-%d.json", mode, repeat)), append(output, '\n'), 0644); err != nil {
						t.Fatal(err)
					}
				}
				t.Logf("mode=%s state=%s events=%d commands=%d rpc=%d failed_rpc=%d integrity=%s reopen=identical", mode, trace.State, trace.Source.EventCount, len(trace.Steps), len(trace.RPCCalls), failedRPC, trace.Integrity.Status)
			})
		}
	}
}
