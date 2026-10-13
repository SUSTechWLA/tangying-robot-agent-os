package capabilityagent

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

// These provider contracts exercise the actual dynamic catalog, planning tool
// selection and model-context projection. An atlas does not need a Go service
// allowlist, and calling a candidate planner never grants motion permission.
type semanticAtlasProvider struct {
	services []*robotv1.ServiceDefinition
	results  map[string]map[string]any
	calls    []*robotv1.ServiceRequest
}

func newSemanticAtlasProvider() *semanticAtlasProvider {
	empty := fields(map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false})
	views := map[string][]any{
		"semantic.map":   {"activeMap", "frameId", "semanticRevision", "cloudSha256", "annotationCount", "coverage", "conflictCount", "categories", "requiresReobservation"},
		"semantic.query": {"activeMap", "frameId", "semanticRevision", "results", "total", "nextOffset", "requiresReobservation"},
		"semantic.plan":  {"activeMap", "frameId", "semanticRevision", "annotationId", "candidates", "executionAuthorized", "requiresReobservation", "requiresFreshLocalization", "requiresDynamicObstacleChecking"},
	}
	p := &semanticAtlasProvider{results: map[string]map[string]any{}}
	for _, name := range []string{"semantic.map", "semantic.query", "semantic.plan"} {
		p.services = append(p.services, &robotv1.ServiceDefinition{Name: name, Available: true, InputSchema: empty,
			Contract: fields(map[string]any{"version": "1", "effects": []any{"READ"}, "planningFields": views[name]})})
		p.results[name] = map[string]any{
			"activeMap": map[string]any{"mapId": "measured-home", "mapRevision": "geometry-1", "calibrationRevision": "cal-1"},
			"frameId":   "map", "semanticRevision": "atlas-2", "requiresReobservation": true,
			// Raw geometric data may exist in a provider response but is not part
			// of the declared semantic planning projection.
			"points": []any{[]any{1., 2., 3.}}, "rgbImage": "raw-image",
		}
	}
	p.results["semantic.map"]["cloudSha256"] = strings.Repeat("a", 64)
	p.results["semantic.map"]["annotationCount"] = 1.
	p.results["semantic.map"]["coverage"] = map[string]any{"annotatedPointCount": 12., "fraction": .25}
	p.results["semantic.map"]["conflictCount"] = 0.
	p.results["semantic.map"]["categories"] = []any{"table"}
	p.results["semantic.query"]["results"] = []any{map[string]any{"id": "table-1", "category": "table", "label": "工作桌", "confidence": .9}}
	p.results["semantic.query"]["total"] = 1.
	p.results["semantic.query"]["nextOffset"] = nil
	p.results["semantic.plan"]["annotationId"] = "table-1"
	p.results["semantic.plan"]["candidates"] = []any{map[string]any{"pose": []any{1., 2., 0., 1., 0., 0., 0.}, "clearanceM": .4}}
	p.results["semantic.plan"]["executionAuthorized"] = false
	p.results["semantic.plan"]["requiresFreshLocalization"] = true
	p.results["semantic.plan"]["requiresDynamicObstacleChecking"] = true
	p.services = append(p.services, &robotv1.ServiceDefinition{Name: "semantic.annotate", Available: true, MutatesWorld: true, InputSchema: empty,
		Contract: fields(map[string]any{"version": "1", "effects": []any{"CONFIG_CHANGE", "ARTIFACT_WRITE"}, "resources": []any{"robot"},
			"verification": map[string]any{"service": "semantic.map", "match": map[string]any{"semanticRevision": "result.semanticRevision"}}})})
	return p
}

func (p *semanticAtlasProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	return &robotv1.ServiceCatalog{RobotId: "atlas-robot", Services: p.services}, nil
}

func (p *semanticAtlasProvider) CallService(_ context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.calls = append(p.calls, request)
	result, ok := p.results[request.Name]
	if !ok {
		return nil, errors.New("unexpected write or unregistered service")
	}
	return &robotv1.ServiceResponse{Ok: true, Result: fields(result)}, nil
}

func TestSemanticAtlasReadsAreDiscoveredAndProjectedWithoutMotionAuthority(t *testing.T) {
	provider := newSemanticAtlasProvider()
	names := []string{"semantic.map", "semantic.query", "semantic.plan"}
	planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		for _, name := range names {
			if !planningToolOffered(request, name) {
				t.Fatalf("registered semantic read %q was not discovered", name)
			}
		}
		if planningToolOffered(request, "semantic.annotate") {
			t.Fatal("annotation writer was exposed before approval")
		}
		if request.Round <= len(names) {
			return actionloop.Decision{Tool: names[request.Round-1], Arguments: map[string]any{}}, nil
		}
		for index, round := range request.History {
			if round.Tool != names[index] || round.Verdict != "READ_OK" || round.ResultDetail["semanticRevision"] != "atlas-2" ||
				round.ResultDetail["points"] != nil || round.ResultDetail["rgbImage"] != nil {
				t.Fatalf("semantic projection lost provenance or leaked sensor data: %+v", round)
			}
		}
		candidate := request.History[2].ResultDetail
		if candidate["executionAuthorized"] != false || candidate["requiresFreshLocalization"] != true || candidate["requiresDynamicObstacleChecking"] != true {
			t.Fatalf("candidate constraints were lost: %+v", candidate)
		}
		return planningProposal("semantic.map"), nil
	})}
	bundle, handled, err := planner.PlanGoal(context.Background(), "理解当前地图中的桌子并查看接近候选")
	if err != nil || !handled || bundle.Capabilities == nil || len(bundle.Capabilities.PlanningTrace) != 4 {
		t.Fatalf("plan=%+v handled=%v err=%v", bundle, handled, err)
	}
	var called []string
	for _, call := range provider.calls {
		called = append(called, call.Name)
		if call.RobotId != "atlas-robot" || call.OperationId != "" || call.OperationLeaseMs != 0 {
			t.Fatalf("planning read acquired incorrect identity or operation authority: %+v", call)
		}
	}
	if !reflect.DeepEqual(called, names) {
		t.Fatalf("planning dispatched unexpected calls: %v", called)
	}
}

func TestSemanticAtlasAnnotationCannotExecuteDuringPlanning(t *testing.T) {
	provider := newSemanticAtlasProvider()
	planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		if planningToolOffered(request, "semantic.annotate") {
			t.Fatal("annotation writer was offered as a read")
		}
		if request.Round == 1 {
			// A custom or compromised decider is checked after selection too.
			return actionloop.Decision{Tool: "semantic.annotate", Arguments: map[string]any{}}, nil
		}
		if request.History[0].Verdict != "REJECTED" {
			t.Fatalf("write selection was not refused: %+v", request.History)
		}
		return planningProposal("semantic.annotate"), nil
	})}
	bundle, handled, err := planner.PlanGoal(context.Background(), "为测得的桌子添加地图标注")
	if err != nil || !handled || len(provider.calls) != 0 || bundle.Capabilities.Calls[0].Tool != "semantic.annotate" ||
		bundle.Capabilities.PlanningTrace[1].Verdict != "FROZEN_FOR_APPROVAL" {
		t.Fatalf("annotation must be proposed, never executed before approval: %+v calls=%v err=%v", bundle, provider.calls, err)
	}
}

func TestSemanticAtlasPlanningRejectsMisdeclaredRawAndOversizedResults(t *testing.T) {
	for _, test := range []struct {
		name   string
		result map[string]any
	}{
		{"dense points", map[string]any{"results": []any{map[string]any{"points": []any{[]any{1., 2., 3.}}}}}},
		{"more than context budget", map[string]any{"results": []any{
			map[string]any{"label": strings.Repeat("a", 1800)}, map[string]any{"label": strings.Repeat("b", 1800)},
			map[string]any{"label": strings.Repeat("c", 1800)}, map[string]any{"label": strings.Repeat("d", 1800)},
			map[string]any{"label": strings.Repeat("e", 1800)},
		}}},
	} {
		t.Run(test.name, func(t *testing.T) {
			provider := newSemanticAtlasProvider()
			provider.results["semantic.query"] = test.result
			planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
				return actionloop.Decision{Tool: "semantic.query", Arguments: map[string]any{}}, nil
			})}
			bundle, handled, err := planner.PlanGoal(context.Background(), "读取地图中标注的物体")
			if !handled || !errors.Is(err, intent.ErrClarificationRequired) || len(provider.calls) != 1 ||
				len(bundle.Capabilities.Calls) != 0 || bundle.Capabilities.PlanningTrace[0].Verdict != "READ_REJECTED" {
				t.Fatalf("raw or oversized result escaped planning boundary: %+v handled=%v err=%v", bundle, handled, err)
			}
		})
	}
}
