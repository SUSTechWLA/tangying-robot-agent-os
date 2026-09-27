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
