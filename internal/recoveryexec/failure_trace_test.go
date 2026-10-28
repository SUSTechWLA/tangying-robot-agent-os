package recoveryexec_test

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

func TestCanceledModelCallKeepsExactTraceForRecord(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var wire string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		wire = string(body)
		cancel()
	}))
	defer server.Close()
	var calls []string
	var recorded recoveryexec.Result
	recordedOnce := false
	e := recoveryexec.Executor{Registry: recoveryexec.MapRegistry{"mapping.status": serviceTool("mapping.status", skills.SafetyReadOnly, false, &calls)}, Observer: observer(),
		Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "cancel-fixture", Client: server.Client()},
		Record: func(ctx context.Context, _ recoveryexec.Request, result recoveryexec.Result, err error) {
			recordedOnce = true
			recorded = result
			if ctx.Err() != nil {
				t.Error("canceled execution erased recording context")
			}
			if _, ok := ctx.Deadline(); !ok {
				t.Error("recording cleanup must stay bounded")
			}
			if !errors.Is(err, context.Canceled) {
				t.Errorf("lost cancellation: %v", err)
			}
		}}
	result, err := e.Execute(ctx, recoveryexec.Request{Action: action(t, "map.read-status"), TaskID: "task-cancel"})
	if !errors.Is(err, context.Canceled) || !recordedOnce || len(result.Rounds) != 1 || len(recorded.Rounds) != 1 || result.Executed || len(calls) != 0 {
		t.Fatalf("partial model failure lost: result=%+v err=%v recorded=%+v", result, err, recorded)
	}
	p := recorded.Rounds[0].Context
	if p == nil || p.ModelCall == nil || p.ModelCall.Outcome != "canceled" {
		t.Fatal("model failure classification lost")
	}
	raw, err := p.ModelRequestBytes()
	if err != nil || string(raw) != wire {
		t.Fatalf("exact failed request lost: %v", err)
	}
}

func TestLaterObservationFailureKeepsEarlierDispatch(t *testing.T) {
	var calls []string
	reads := 0
	broken := errors.New("sensor unavailable")
	e := recoveryexec.Executor{Registry: recoveryexec.MapRegistry{"mapping.status": serviceTool("mapping.status", skills.SafetyReadOnly, false, &calls)},
		Observer: observerFunc(func(context.Context, string) (actionloop.Observation, error) {
			reads++
			if reads > 1 {
				return actionloop.Observation{}, broken
			}
			return actionloop.Observation{Summary: "read status"}, nil
		}), Decider: &scripted{decisions: []actionloop.Decision{{Tool: "mapping.status", Arguments: map[string]any{"parameters": "{}"}}}},
		Record: func(_ context.Context, _ recoveryexec.Request, r recoveryexec.Result, err error) {
			if !r.Executed || len(r.Rounds) != 1 || !errors.Is(err, broken) {
				t.Errorf("record lost earlier dispatch: %+v %v", r, err)
			}
		}}
	result, err := e.Execute(context.Background(), recoveryexec.Request{Action: action(t, "map.read-status")})
	if !errors.Is(err, broken) || !result.Executed || result.Verified || len(result.Rounds) != 1 || len(calls) != 1 {
		t.Fatalf("failure rewrote executed history: %+v %v", result, err)
	}
}
