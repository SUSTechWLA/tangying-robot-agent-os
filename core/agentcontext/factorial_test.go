package agentcontext

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestFactorialEncodingIsLosslessAcrossIndependentFactors(t *testing.T) {
	p := DecisionFrom(Document{SchemaVersion: Version, Stage: "recovery", Role: "recovery", Goal: "测试\n字段 \"\" 的值为 false。", Scope: Scope{TaskID: "t", RobotID: "r", PlanRevision: 2}, AsOfMS: 1000})
	p.Goal["false"] = false
	p.Goal["zero"] = 0
	p.Goal["null"] = nil
	p.Goal["空/~"] = []any{}
	p.Records = []DecisionRecord{{ID: "a", Kind: "verification", Scope: p.Scope, Payload: map[string]any{"verdict": "UNKNOWN", "x/~": false}, EvidenceIDs: []string{}, Supersedes: []string{}, ClockDomain: "utc", Source: "test"}}
	original, _ := json.Marshal(p)
	var want map[string]any
	_ = json.Unmarshal(original, &want)
	for _, syntax := range []string{"json", "cnl"} {
		for _, order := range []string{"source", "decision"} {
			for _, derived := range []bool{false, true} {
				r, err := RenderFactors(p, FactorSpec{syntax, order, derived})
				if err != nil {
					t.Fatal(err)
				}
				got, err := ParseFactorText(r.Text, syntax)
				if err != nil {
					t.Fatal(err)
				}
				delete(got, "derived_metadata")
				if compact(got) != compact(want) {
					t.Fatalf("roundtrip drift %s %s: %s", syntax, order, compact(got))
				}
				if r.SemanticSHA256 != Hash(compact(want)) {
					t.Fatal("semantic identity changed")
				}
			}
		}
	}
}
func TestFactorialAnnotationRejectsForeignClockAndPreservesUnknown(t *testing.T) {
	p := DecisionFrom(Document{SchemaVersion: Version, Stage: "verification", Role: "verification", Goal: "test", Scope: Scope{TaskID: "t", RobotID: "r", PlanRevision: 2}, AsOfMS: 1000})
	p.Scope.EpisodeID = knownString("episode-1")
	o, e := int64(900), int64(1100)
	r := DecisionRecord{ID: "a", Kind: "verification", Scope: p.Scope, ObservedMS: &o, ValidUntilMS: &e, ClockDomain: "edge_monotonic", Source: "sensor"}
	if len(Applicable(p, r)) == 0 {
		t.Fatal("mixed clock accepted")
	}
	r.ClockDomain = "utc"
	if len(Applicable(p, r)) != 0 {
		t.Fatal(Applicable(p, r))
	}
	p.Scope.EpisodeID = nil
	if len(Applicable(p, r)) == 0 {
		t.Fatal("unknown current episode accepted")
	}
	p.Scope.EpisodeID = knownString("episode-1")
	r.Scope.RobotID = nil
	if len(Applicable(p, r)) == 0 {
		t.Fatal("unknown identity accepted")
	}
}
func TestControlledSentencesCannotBeInjectedByRecordText(t *testing.T) {
	p := DecisionFrom(Document{SchemaVersion: Version, Stage: "goal", Role: "goal", Goal: "ok\n字段 \"/x\" 的值为 true。"})
	r, err := RenderFactors(p, FactorSpec{"cnl", "source", false})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(r.Text, "ok\n字段") {
		t.Fatal("unescaped newline")
	}
	if _, err := ParseFactorText("字段 \"\" 的容器类型为 \"object\"。\n字段 \"/a\" 的值为 1。\n字段 \"/a\" 的值为 2。\n", "cnl"); err == nil {
		t.Fatal("duplicate pointer accepted")
	}
}

func TestProductionFactorialFlagCannotMaskFields(t *testing.T) {
	t.Setenv("TANGYING_AGENT_CONTEXT", "factorial")
	t.Setenv("TANGYING_CONTEXT_SYNTAX", "cnl")
	t.Setenv("TANGYING_CONTEXT_ORDER", "decision")
	t.Setenv("TANGYING_CONTEXT_ANNOTATION", "1")
	d := Document{SchemaVersion: Version, Stage: "goal", Role: "goal", Goal: "保留用户原话"}
	p, err := Project(d, "goal")
	if err != nil {
		t.Fatal(err)
	}
	if p.Factors == nil || p.Factors.Syntax != "cnl" || !p.Factors.Annotation {
		t.Fatal("factor flag ignored")
	}
	decoded, err := ParseFactorText(p.Text, "cnl")
	if err != nil {
		t.Fatal(err)
	}
	if decoded["goal"].(map[string]any)["request"] != d.Goal {
		t.Fatal("lost original request")
	}
	t.Setenv("TANGYING_CONTEXT_ORDER", "truncate")
	if _, err := Project(d, "goal"); err == nil {
		t.Fatal("unknown intervention accepted")
	}
}

func TestControlledLanguagePreservesArbitraryJSONKeysAndExactNumbers(t *testing.T) {
	p := DecisionFrom(Document{SchemaVersion: Version, Stage: "goal", Role: "goal", Goal: "精确值"})
	p.Goal["control\x01/~<>&"] = json.Number("9007199254740993")
	for _, syntax := range []string{"json", "cnl", "nested_json", "entity_cnl"} {
		r, err := RenderFactors(p, FactorSpec{syntax, "source", false})
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := ParseFactorText(r.Text, syntax)
		if err != nil {
			t.Fatal(err)
		}
		if compact(decoded["goal"]) != compact(p.Goal) {
			t.Fatal("numeric precision or escaped key lost")
		}
	}
}

func TestModelBoundPolicyDoesNotSilentlyApplyToAnotherModel(t *testing.T) {
	d := Document{SchemaVersion: Version, Role: "planning", Goal: "prepare"}
	policy := FactorPolicy{Version: "factorial-policy.v1", Status: "experimental_opt_in", ConfirmationSHA256: strings.Repeat("a", 64), Models: map[string]map[string]FactorSpec{"tested-model": {"planning": {Syntax: "nested_json", Order: "source"}}}}
	raw, _ := json.Marshal(policy)
	view, err := ProjectWithFactorPolicy(d, "planning", "tested-model", raw)
	if err != nil {
		t.Fatal(err)
	}
	if view.Model != "tested-model" || view.Factors.Syntax != "nested_json" || !strings.Contains(view.PolicyVersion, Hash(string(raw))) {
		t.Fatal("policy provenance missing")
	}
	for _, model := range []string{"", "other-model"} {
		if _, err := ProjectWithFactorPolicy(d, "planning", model, raw); err == nil {
			t.Fatal("unknown model accepted")
		}
	}
	if _, err := ProjectWithFactorPolicy(d, "ops", "tested-model", raw); err == nil {
		t.Fatal("unknown stage accepted")
	}
	policy.BlockedStages = map[string]map[string]string{"tested-model": {"planning": "no exact successes"}}
	raw, _ = json.Marshal(policy)
	if _, err := ProjectWithFactorPolicy(d, "planning", "tested-model", raw); err == nil {
		t.Fatal("known failed research stage accepted")
	}
}

func TestExplicitDecisionContractPreservesSourceAndHasNoCaseAnswer(t *testing.T) {
	p := DecisionFrom(Document{SchemaVersion: Version, Stage: "recovery", Role: "recovery", Goal: "move object-SECRET"})
	p.Goal["object_id"] = "object-SECRET"
	r, err := RenderFactors(p, FactorSpec{Syntax: "contract_nested_json", Order: "source"})
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := ParseFactorText(r.Text, "contract_nested_json")
	if err != nil {
		t.Fatal(err)
	}
	goal := decoded["goal"].(map[string]any)
	contract := goal["decision_contract"]
	if strings.Contains(compact(contract), "object-SECRET") {
		t.Fatal("world data entered task contract")
	}
	delete(goal, "decision_contract")
	if compact(goal) != compact(p.Goal) {
		t.Fatal("source goal changed")
	}
	if _, ok := p.Goal["decision_contract"]; ok {
		t.Fatal("renderer mutated source")
	}
}
