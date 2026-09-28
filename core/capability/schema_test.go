package capability

import "testing"

func TestSchemasRejectUnenforcedAndMalformedConstraints(t *testing.T) {
	for _, schema := range []map[string]any{
		{"type": 42}, {"type": "string", "maxLength": "2"}, {"type": "number", "minimum": 4, "maximum": 2},
		{"type": "object", "additionalProperties": map[string]any{"type": "string"}},
		{"type": "object", "required": []string{"missing"}}, {"type": "array", "items": map[string]any{"oneOf": []any{}}},
	} {
		if err := ValidateSchema(schema); err == nil {
			t.Fatalf("invalid schema accepted: %#v", schema)
		}
	}
}
func TestTransportNumbersEnumsAndUnicodeConstraints(t *testing.T) {
	schema := map[string]any{"type": "object", "properties": map[string]any{"budget": map[string]any{"type": "integer", "enum": []any{float64(2)}}, "label": map[string]any{"type": "string", "minLength": 1, "maxLength": 2}}, "required": []string{"budget", "label"}, "additionalProperties": false}
	if err := Validate(map[string]any{"budget": 2, "label": "厨房"}, schema); err != nil {
		t.Fatal(err)
	}
	for _, v := range []map[string]any{{"budget": 2.5, "label": "厨房"}, {"budget": 2, "label": ""}, {"budget": 2, "label": "厨房门"}, {"budget": 2, "label": "厨房", "authority": true}} {
		if err := Validate(v, schema); err == nil {
			t.Fatalf("invalid arguments accepted: %#v", v)
		}
	}
}

func TestOneOfRejectsCrossModeArgumentsBeforeDispatch(t *testing.T) {
	schema := map[string]any{"oneOf": []any{
		map[string]any{"type": "object", "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{"survey"}}}, "required": []any{"mode"}, "additionalProperties": false},
		map[string]any{"type": "object", "properties": map[string]any{"mode": map[string]any{"type": "string", "enum": []any{"explore"}}, "maxLegs": map[string]any{"type": "integer", "minimum": 1, "maximum": 6}}, "additionalProperties": false},
	}}
	for _, args := range []map[string]any{{"mode": "survey"}, {"mode": "explore", "maxLegs": float64(2)}, {}} {
		if err := Validate(args, schema); err != nil {
			t.Fatalf("valid mode rejected: %v", err)
		}
	}
	for _, args := range []map[string]any{{"mode": "survey", "maxLegs": 2}, {"mode": "explore", "maxLegs": 2.5}} {
		if err := Validate(args, schema); err == nil {
			t.Fatalf("invalid mode arguments accepted: %#v", args)
		}
	}
	if err := Validate(1, map[string]any{"oneOf": []any{map[string]any{"type": "number"}, map[string]any{"type": "integer"}}}); err == nil {
		t.Fatal("overlapping oneOf branches must not match twice")
	}
}
func TestEffectsCannotContradictMutationFlag(t *testing.T) {
	for _, mutation := range []bool{false, true} {
		effect := "READ"
		if !mutation {
			effect = "PHYSICAL_MOTION"
		}
		m := Manifest{Name: "unsafe", MutatesWorld: mutation, InputSchema: map[string]any{"type": "object"}, Contract: Contract{Version: "1", Effects: []string{effect}}}
		if err := m.Validate(); err == nil {
			t.Fatal("contradictory effect contract accepted")
		}
	}
}

func TestWriterNeedsIndependentCompletionEvenWithOperationTerminal(t *testing.T) {
	m := Manifest{Name: "drive", MutatesWorld: true, InputSchema: map[string]any{"type": "object"}, Contract: Contract{Version: "1", Effects: []string{"PHYSICAL_MOTION"}, Resources: []string{"robot"}, Operation: &Operation{StatusService: "status", CancelService: "cancel", IdentityPath: "id", StatusIdentityPath: "id", StatePath: "state", Running: []string{"running"}, Success: []string{"complete"}, Failure: []string{"failed"}}}}
	if err := m.Validate(); err == nil {
		t.Fatal("a controller terminal bit alone established physical completion")
	}
	m.Contract.Verification = &Verification{Service: "observe", Required: []string{"poseRevision"}}
	if err := m.Validate(); err != nil {
		t.Fatal(err)
	}
	m.Contract.Operation.Failure = []string{"complete"}
	if err := m.Validate(); err == nil {
		t.Fatal("overlapping operation states accepted")
	}
}
