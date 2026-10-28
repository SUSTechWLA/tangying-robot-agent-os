package console_test

import (
	"context"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/types/known/structpb"
)

type navigationStatusProvider struct {
	result map[string]any
	err    error
	calls  []string
}

func (p *navigationStatusProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	return &robotv1.ServiceCatalog{RobotId: "xlerobot-0001"}, nil
}
func (p *navigationStatusProvider) CallService(_ context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.calls = append(p.calls, r.Name)
	if p.err != nil {
		return nil, p.err
	}
	s, _ := structpb.NewStruct(p.result)
	return &robotv1.ServiceResponse{Ok: true, Result: s}, nil
}
func TestRuntimeNavigationStatusBlocksReadyMapWhenDriverIsDown(t *testing.T) {
	for _, tc := range []struct {
		name   string
		result map[string]any
		err    error
		state  string
		ready  bool
	}{
		{"driver active", map[string]any{"ready": true, "navigationDriver": map[string]any{"ready": true}}, nil, "ready", true},
		{"driver down", map[string]any{"ready": false, "navigationDriver": map[string]any{"ready": false, "blockers": []any{map[string]any{"code": "NAV2_INACTIVE", "message": "controller is inactive"}}}}, nil, "action", false},
		{"contradictory top ready", map[string]any{"ready": true, "navigationDriver": map[string]any{"ready": false}}, nil, "action", false},
		{"missing readiness", map[string]any{"mapId": "active-map"}, nil, "unknown", false},
		{"malformed driver", map[string]any{"ready": true, "navigationDriver": map[string]any{"ready": "true"}}, nil, "unknown", false},
		{"transport unavailable", nil, errors.New("unavailable"), "unknown", false},
		{"generic compatible status", map[string]any{"ready": true}, nil, "ready", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
			app := localapp.New(service, agent.NewRunner(nil, nil, nil), memory.NewQueue[string](1))
			app.WithSupervision(watching)
			provider := &navigationStatusProvider{result: tc.result, err: tc.err}
			server := httptest.NewServer(console.NewServer(service, app, console.WithRuntime(fakeRuntime{snapshot: healthyRobot()}), console.WithRobotServices(provider)).Handler())
			defer server.Close()
			view := getReadiness(t, server)
			nav := checkByID(view, "navigation")
			if view.Ready != tc.ready || nav.State != tc.state || !nav.Blocking || checkByID(view, "map").State != "ready" {
				t.Fatalf("map must not mask driver status: %+v", view)
			}
			if len(provider.calls) != 1 || provider.calls[0] != "navigation.status" {
				t.Fatalf("readiness started unexpected action: %v", provider.calls)
			}
			if tc.name == "driver down" && !strings.Contains(nav.Detail, "NAV2_INACTIVE") {
				t.Fatalf("blocker lost: %+v", nav)
			}
		})
	}
}
func TestLanguageReadinessSelectsConfiguredGoalBeforeDeterministicIntent(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	config := console.ConfigStatus{Provider: "deterministic", Stages: map[string]console.ModelStageStatus{"goal": {Provider: "openai", BaseURL: "http://model.invalid/v1", Model: "goal-model"}, "intent": {Provider: "deterministic"}}}
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}, console.WithSettings(&settingsStub{status: config})).Handler())
	defer server.Close()
	view := getReadiness(t, server)
	if !view.Language.ModelConfigured || view.Language.Provider != "openai" {
		t.Fatalf("GOAL configured but language reports offline: %+v", view.Language)
	}
}

func TestNavigationFailureDoesNotRecommendRebuildingAnExistingMap(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	app := localapp.New(service, agent.NewRunner(nil, nil, nil), memory.NewQueue[string](1))
	app.WithSupervision(watching)
	robot := healthyRobot()
	robot.Capabilities[0].Available = false
	robot.Capabilities[0].Blockers = []string{"chassis:NAV_MAP_NOT_READY"}
	provider := &navigationStatusProvider{result: map[string]any{
		"ready": false, "mapId": "existing-complete-map", "mapRevision": "revision",
		"navigationDriver": map[string]any{"ready": false, "blockers": []any{
			map[string]any{"code": "NAV2_INACTIVE", "message": "controller is inactive"},
		}},
	}}
	server := httptest.NewServer(console.NewServer(service, app,
		console.WithRuntime(fakeRuntime{snapshot: robot}), console.WithRobotServices(provider)).Handler())
	defer server.Close()
	view := getReadiness(t, server)
	m := checkByID(view, "map")
	if view.Ready || m.State != "action" || !strings.Contains(m.Detail, "NAV2_INACTIVE") ||
		!strings.Contains(m.Action, "导航运行依赖") {
		t.Fatalf("map/driver distinction missing: %+v", view)
	}
	for _, wrong := range []string{"没有启用", "地图还不完整", "巡检建图", "走一遍"} {
		if strings.Contains(m.Situation+m.Action, wrong) {
			t.Fatalf("driver failure became a mapping instruction: %+v", m)
		}
	}
}
