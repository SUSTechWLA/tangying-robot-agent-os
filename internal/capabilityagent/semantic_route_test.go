package capabilityagent

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func semanticRouteFixture() (map[string]any, []capability.PlanningStep) {
	r := strings.Repeat("a", 64)
	identity := map[string]any{"robotId": "atlas-robot", "mapId": "map-a", "mapRevision": r, "calibrationRevision": r, "cloudSha256": r, "frameId": "map"}
	target := func(id, name string) any {
		return map[string]any{"annotationId": id, "name": name, "maxDistanceM": .65, "allowStaleSearch": false, "verificationMode": "arrival"}
	}
	args := map[string]any{"semanticRoute": map[string]any{"mapId": "map-a", "mapRevision": r, "calibrationRevision": r, "cloudSha256": r, "semanticRevision": r,
		"targets": []any{target("north", "北侧检测区"), target("kitchen-table", "厨房侧桌"), target("north", "北侧检测区")}}}
	rows := []any{map[string]any{"id": "north", "name": "北侧检测区", "kind": "region", "confidence": .9, "stale": false, "ambiguous": false},
		map[string]any{"id": "kitchen-table", "name": "厨房侧桌", "kind": "object", "confidence": .9, "stale": false, "ambiguous": false}}
	trace := []capability.PlanningStep{{Tool: "semantic.query", Verdict: "READ_OK", Arguments: map[string]any{},
		Result: map[string]any{"identity": identity, "semanticRevision": r, "results": rows, "total": float64(2)}}}
	return args, trace
}

func TestSemanticRouteFreezesVerifiedNamesIntoOrderedBoundCheckpoints(t *testing.T) {
	args, trace := semanticRouteFixture()
	provider := newSemanticAtlasProvider()
	provider.services = []*robotv1.ServiceDefinition{{Name: "semantic.query", Available: true,
		InputSchema: fields(map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false}),
		Contract:    fields(map[string]any{"version": "1", "effects": []any{"READ"}, "planningFields": []any{"identity", "semanticRevision", "results", "total"}})}}
	provider.results["semantic.query"] = trace[0].Result
	p := Planner{Provider: provider, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		if request.Round == 1 {
			return actionloop.Decision{Tool: "semantic.query", Arguments: map[string]any{}}, nil
		}
		return actionloop.Decision{Tool: "propose_capability_plan", Arguments: map[string]any{"calls": []any{map[string]any{"tool": "robot.task", "arguments": args}}}}, nil
	})}
	bundle, handled, err := p.PlanGoal(context.Background(), "去北侧检测区，再去厨房侧桌，最后再次去北侧检测区取得新帧")
	if err != nil || !handled {
		t.Fatalf("plan: %v", err)
	}
	var parsed manipulation.Intent
	if err = json.Unmarshal(bundle.Capabilities.Calls[0].LegacyIntent, &parsed); err != nil {
		t.Fatal(err)
	}
	if len(parsed.Sequence) != 3 || parsed.Sequence[0].SemanticTarget.AnnotationID != "north" || parsed.Sequence[2].SemanticTarget.AnnotationID != "north" ||
		parsed.Sequence[1].SemanticTarget.Identity.CloudSHA256 != strings.Repeat("a", 64) {
		t.Fatalf("lost route: %+v", parsed)
	}
	for _, item := range parsed.Sequence {
		if len(item.RouteRooms) != 1 || item.RobotID != "atlas-robot" || item.SemanticTarget == nil {
			t.Fatalf("unbound stage: %+v", item)
		}
	}
	if len(provider.calls) != 1 || provider.calls[0].Name != "semantic.query" {
		t.Fatalf("planning performed effects: %+v", provider.calls)
	}
}

func TestSemanticRouteRejectsInventedOrOmittedTargetEvidence(t *testing.T) {
	for _, name := range []string{"missing read", "wrong robot", "wrong revision", "invented name", "wrong id", "stale", "conflict", "unrequested target", "omitted target", "omitted repeated visit", "reordered visits", "implicit return", "region object verification"} {
		t.Run(name, func(t *testing.T) {
			args, trace := semanticRouteFixture()
			route := args["semanticRoute"].(map[string]any)
			targets := route["targets"].([]any)
			first := targets[0].(map[string]any)
			row := trace[0].Result["results"].([]any)[0].(map[string]any)
			goal := "去北侧检测区，再去厨房侧桌，最后再次去北侧检测区"
			switch name {
			case "missing read":
				trace = nil
			case "wrong robot":
				trace[0].Result["identity"].(map[string]any)["robotId"] = "other"
			case "wrong revision":
				route["semanticRevision"] = strings.Repeat("b", 64)
			case "invented name":
				first["name"] = "未经验证的房间"
			case "wrong id":
				first["annotationId"] = "invented"
			case "stale":
				row["stale"] = true
			case "conflict":
				row["ambiguous"] = true
			case "unrequested target":
				goal = "去北侧检测区"
			case "omitted target":
				route["targets"] = []any{targets[0]}
			case "omitted repeated visit":
				route["targets"] = targets[:2]
			case "reordered visits":
				route["targets"] = []any{targets[1], targets[0], targets[2]}
			case "implicit return":
				goal += "然后返回起点"
			case "region object verification":
				first["verificationMode"] = "object"
			}
			if _, _, err := freezeSemanticRoute(args, goal, "atlas-robot", trace); err == nil {
				t.Fatal("unsafe route accepted")
			}
		})
	}
}

func TestSemanticCompositeSchemaForbidsCoordinatesAndMixedModes(t *testing.T) {
	args, _ := semanticRouteFixture()
	if err := capability.Validate(args, compositeInputSchema()); err != nil {
		t.Fatal(err)
	}
	args["request"] = "去厨房"
	if err := capability.Validate(args, compositeInputSchema()); err == nil {
		t.Fatal("mixed request and structured route accepted")
	}
	delete(args, "request")
	args["semanticRoute"].(map[string]any)["targets"].([]any)[0].(map[string]any)["goalPose"] = []any{1., 2., 0.}
	if err := capability.Validate(args, compositeInputSchema()); err == nil {
		t.Fatal("model coordinate accepted")
	}
}

func TestSemanticRouteAllowsExplicitStaleSearchAndCertifiedAlias(t *testing.T) {
	args, trace := semanticRouteFixture()
	route := args["semanticRoute"].(map[string]any)
	first := route["targets"].([]any)[0].(map[string]any)
	first["name"], first["allowStaleSearch"] = "北区", true
	route["targets"] = []any{first}
	row := trace[0].Result["results"].([]any)[0].(map[string]any)
	row["stale"] = true
	trace[0].Result["results"], trace[0].Result["total"] = []any{row}, float64(1)
	trace[0].Arguments = map[string]any{"name": "北区"}
	if _, _, err := freezeSemanticRoute(args, "到北区按旧位置搜索", "atlas-robot", trace); err != nil {
		t.Fatal(err)
	}
}
