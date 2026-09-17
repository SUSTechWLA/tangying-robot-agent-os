package localconfig_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localconfig"
)

// A settings screen whose save button produces "restart to apply" is a settings
// screen that is really a deployment form. The operator changed a value; the value
// should change.
func TestASavedSettingIsAppliedWithoutARestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	applied := []console.ConfigStatus{}
	settings := localconfig.NewSettings(path, console.ConfigStatus{Provider: "deterministic"}).
		WithOnChange(func(status console.ConfigStatus) { applied = append(applied, status) })

	if err := settings.UpdateLLM(console.LLMConfig{
		Provider: "openai", BaseURL: "https://example.invalid/v1",
		Model: "a-model", APIKey: "secret",
	}); err != nil {
		t.Fatalf("update: %v", err)
	}
	if len(applied) != 1 {
		t.Fatalf("the change was applied %d times, want once", len(applied))
	}
	if applied[0].Model != "a-model" {
		t.Fatalf("applied status = %+v", applied[0])
	}
	// And the caller is told it took effect, not asked to restart.
	if settings.Status().RestartRequired {
		t.Fatal("a change that was applied still asks for a restart")
	}
}

// Without a callback there is nothing to apply it, so the honest answer is still
// "restart".
//
// Claiming a setting took effect when nothing was told is worse than asking for a
// restart: the operator would believe the model was in use and it would not be.
func TestWithoutACallbackARestartIsStillRequired(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	settings := localconfig.NewSettings(path, console.ConfigStatus{Provider: "deterministic"})
	if err := settings.UpdateLLM(console.LLMConfig{
		Provider: "openai", BaseURL: "https://example.invalid/v1",
		Model: "a-model", APIKey: "secret",
	}); err != nil {
		t.Fatalf("update: %v", err)
	}
	if !settings.Status().RestartRequired {
		t.Fatal("a change nobody applied reported itself as applied")
	}
}

// A rejected update must not be applied, and must not be written.
func TestARejectedUpdateChangesNothing(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	if err := os.WriteFile(path, []byte("AGENT_MODEL=original\n"), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	applied := 0
	settings := localconfig.NewSettings(path, console.ConfigStatus{Provider: "deterministic"}).
		WithOnChange(func(console.ConfigStatus) { applied++ })

	// openai needs all three of baseUrl, model and apiKey.
	if err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", BaseURL: "https://x.invalid/v1"}); err == nil {
		t.Fatal("an incomplete openai configuration was accepted")
	}
	if applied != 0 {
		t.Fatal("a rejected configuration was applied")
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !strings.Contains(string(contents), "AGENT_MODEL=original") {
		t.Fatalf("a rejected update changed the file:\n%s", contents)
	}
}

// The secret is never returned through the status API.
func TestTheAPIKeyIsNeverReturned(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	settings := localconfig.NewSettings(path, console.ConfigStatus{Provider: "deterministic"})
	if err := settings.UpdateLLM(console.LLMConfig{
		Provider: "openai", BaseURL: "https://example.invalid/v1",
		Model: "a-model", APIKey: "super-secret-value",
	}); err != nil {
		t.Fatalf("update: %v", err)
	}
	status := settings.Status()
	if !status.HasAPIKey {
		t.Fatal("the status does not say a key is configured")
	}
	encoded := strings.Join([]string{status.Provider, status.BaseURL, status.Model}, " ")
	if strings.Contains(encoded, "super-secret-value") {
		t.Fatalf("the status leaks the key: %s", encoded)
	}
}
