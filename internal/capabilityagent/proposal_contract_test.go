package capabilityagent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

func TestProposalContractKeepsEveryConstraintAcrossLargeCatalog(t *testing.T) {
	entries := map[string]capability.Manifest{}
	for index := 0; index < 65; index++ {
		name := fmt.Sprintf("module.service_%02d", index)
		entries[name] = capability.Manifest{Name: name, Description: "Registered module", InputSchema: map[string]any{
			"type": "object", "additionalProperties": false, "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{fmt.Sprint(index)}}}, "required": []any{"mode"}}}
	}
	fingerprint := capability.Fingerprint(entries)
	schema, description := proposalContract(entries)
	if err := capability.ValidateSchema(schema); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(description, "inputSchema") || strings.Contains(description, "additionalProperties") {
		t.Fatal("schema was duplicated in the context description")
	}
	for index := 0; index < 65; index++ {
		name := fmt.Sprintf("module.service_%02d", index)
		arguments := map[string]any{"mode": fmt.Sprint(index)}
		proposal := map[string]any{"calls": []any{map[string]any{"tool": name, "arguments": arguments}}}
		if err := capability.Validate(proposal, schema); err != nil {
			t.Fatalf("catalog member %d disappeared: %v", index, err)
		}
		arguments["mode"] = "not-in-registered-enum"
		if err := capability.Validate(proposal, schema); err == nil {
			t.Fatalf("member %d lost enum constraint", index)
		}
		arguments["mode"] = fmt.Sprint(index)
		arguments["invented"] = true
		if err := capability.Validate(proposal, schema); err == nil {
			t.Fatalf("member %d lost closed argument schema", index)
		}
	}
	if capability.Fingerprint(entries) != fingerprint {
		t.Fatal("presentation rewrote the frozen execution catalog")
	}
}

func fullPlanningFixture() (*semanticAtlasProvider, map[string]any) {
	args, trace := semanticRouteFixture()
	p := newSemanticAtlasProvider()
	p.results["semantic.query"] = trace[0].Result
	// Two bounded records total roughly the 4.2 KiB measured query in the failed
	// five-checkpoint run; no specific model messages or experiment data copied.
	for _, raw := range p.results["semantic.query"]["results"].([]any) {
		raw.(map[string]any)["source"] = map[string]any{"reference": strings.Repeat("operator evidence ", 95)}
	}
	for _, service := range p.services {
		if service.Name == "semantic.query" {
			service.Contract = fields(map[string]any{"version": "1", "effects": []any{"READ"}, "planningFields": []any{"identity", "semanticRevision", "results", "total"}})
		}
	}
	empty := map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{}}
	for index := 0; index < 15; index++ {
		name := fmt.Sprintf("registered.read_%02d", index)
		if index == 0 {
			name = "navigation.status"
		}
		p.services = append(p.services, &robotv1.ServiceDefinition{Name: name, Available: true, Description: strings.Repeat("读取本模块可追溯状态；", 10), InputSchema: fields(empty),
			Contract: fields(map[string]any{"version": "1", "effects": []any{"READ"}, "planningFields": []any{"state", "revision", "ready"}, "outputSchema": map[string]any{"type": "object", "additionalProperties": true}})})
		p.results[name] = map[string]any{"state": "ready", "ready": true, "revision": strings.Repeat("a", 64)}
	}
	for index := 0; index < 5; index++ {
		properties := map[string]any{}
		for field := 0; field < 10; field++ {
			properties[fmt.Sprintf("parameter_%02d", field)] = map[string]any{"type": "string", "minLength": 1, "maxLength": 128}
		}
		p.services = append(p.services, &robotv1.ServiceDefinition{Name: fmt.Sprintf("registered.write_%02d", index), Available: true, MutatesWorld: true,
			Description: "Provider-owned configuration operation requiring approval and independent verification",
			InputSchema: fields(map[string]any{"type": "object", "additionalProperties": false, "properties": properties}),
			Contract: fields(map[string]any{"version": "1", "effects": []any{"CONFIG_CHANGE"}, "resources": []any{"robot"},
				"verification": map[string]any{"service": "navigation.status", "required": []any{"ready"}}, "outputSchema": map[string]any{"type": "object", "additionalProperties": true}})})
	}
	return p, args
}

func TestSemanticQueryAndFullCatalogFitDefaultPlanningRequestBudget(t *testing.T) {
	p, args := fullPlanningFixture()
	requests, maxBytes := 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		if len(body) > maxBytes {
			maxBytes = len(body)
		}
		requests++
		tool, arguments := "navigation.status", map[string]any{}
		switch requests {
		case 2:
			tool = "semantic.map"
		case 3:
			tool = "semantic.query"
		case 4:
			tool = "propose_capability_plan"
			arguments = map[string]any{"calls": []any{map[string]any{"tool": "robot.task", "arguments": args}}}
		}
		var sent struct {
			Tools []struct {
				Function struct {
					Name        string `json:"name"`
					Description string `json:"description"`
				} `json:"function"`
			} `json:"tools"`
		}
		if err := json.Unmarshal(body, &sent); err != nil {
			t.Fatal(err)
		}
		for _, entry := range sent.Tools {
			if strings.HasPrefix(entry.Function.Description, "系统工具："+tool+"。") {
				tool = entry.Function.Name
				break
			}
		}
		wire, _ := json.Marshal(arguments)
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{"tool_calls": []any{map[string]any{
			"id": "planning-call", "type": "function", "function": map[string]any{"name": tool, "arguments": string(wire)}}}}}}})
	}))
	defer server.Close()
	planner := Planner{Provider: p, Decider: &actionloop.LLMDecider{BaseURL: server.URL, Model: "fixture"}}
	bundle, handled, err := planner.PlanGoal(context.Background(), "先读取导航状态，再去北侧检测区，接着去厨房侧桌，最后再次去北侧检测区")
	if err != nil || !handled || requests != 4 {
		t.Fatalf("requests=%d bytes=%d err=%v", requests, maxBytes, err)
	}
	if maxBytes > 65536 {
		t.Fatalf("request exceeded unchanged budget: %d", maxBytes)
	}
	maxContext := 0
	for _, step := range bundle.Capabilities.PlanningTrace {
		if step.Context != nil && len(step.Context.Text) > maxContext {
			maxContext = len(step.Context.Text)
		}
	}
	if maxContext > 32768 {
		t.Fatalf("context exceeded unchanged budget: %d", maxContext)
	}
	t.Logf("catalog=%d services, model_request_max=%d bytes, context_max=%d bytes", len(p.services)+1, maxBytes, maxContext)
}

func TestTightPlanningBudgetRefusesWithoutDroppingCatalogOrDispatching(t *testing.T) {
	t.Setenv("TANGYING_CONTEXT_MAX_BYTES", "4096")
	t.Setenv("TANGYING_MODEL_REQUEST_MAX_BYTES", "8192")
	p, _ := fullPlanningFixture()
	_, entries, err := Catalogue(t.Context(), p)
	if err != nil {
		t.Fatal(err)
	}
	expected := capability.Fingerprint(entries)
	called := false
	planner := Planner{Provider: p, Decider: contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
		called = true
		return actionloop.Decision{}, nil
	})}
	bundle, _, err := planner.PlanGoal(t.Context(), "检查所有已注册模块")
	if err == nil || !strings.Contains(err.Error(), "context budget exceeded") || called || len(p.calls) != 0 {
		t.Fatalf("budget refusal failed: %v called=%t reads=%d", err, called, len(p.calls))
	}
	if bundle.Capabilities.CatalogRevision != expected || len(bundle.Capabilities.Calls) != 0 {
		t.Fatal("budget pressure changed authority or left executable calls")
	}
}
