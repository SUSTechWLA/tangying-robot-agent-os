package exchangecontract

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGoldenEnvelope(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "agent-data-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := ValidateJSON(raw); err != nil {
		t.Fatal(err)
	}
	var e Envelope
	if err := json.Unmarshal(raw, &e); err != nil {
		t.Fatal(err)
	}
	if err := e.Validate(); err != nil {
		t.Fatal(err)
	}
	if e.Sequence != 9007199254740993 {
		t.Fatal("sequence precision was lost")
	}
}

func TestRejectDuplicateAndExcessiveJSON(t *testing.T) {
	for _, raw := range []string{`{"a":1,"a":2}`, `{"a":{"value":1,"value":2}}`, `{} {}`, strings.Repeat("[", 34) + "0" + strings.Repeat("]", 34)} {
		if err := ValidateJSON([]byte(raw)); err == nil {
			t.Errorf("accepted %s", raw)
		}
	}
	if _, err := CanonicalPayload([]byte(`{"a":1,"a":2}`)); err == nil {
		t.Fatal("canonicalized duplicate fields")
	}
}
