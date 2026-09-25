package localconfig_test

import (
	"errors"
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
		WithOnChange(func(status console.ConfigStatus) error { applied = append(applied, status); return nil })
	if len(settings.Status().Stages) != 3 {
		t.Fatal("a fresh installation must show all three model stages")
	}

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

func TestKeylessLocalModelCanBeSavedAndApplied(t *testing.T) {
	settings := localconfig.NewSettings(filepath.Join(t.TempDir(), "local.env"), console.ConfigStatus{Provider: "deterministic"}).
		WithOnChange(func(console.ConfigStatus) error { return nil })
	if err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", BaseURL: "http://127.0.0.1:8000/v1", Model: "small-quantized"}); err != nil {
		t.Fatal(err)
	}
	status := settings.Status()
	if status.HasAPIKey || status.RestartRequired || status.Model != "small-quantized" {
		t.Fatalf("keyless status: %+v", status)
	}
}

func TestFailedLiveApplyRestoresPreviousConfiguration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	oldContents := []byte("# operator note\nAGENT_PROVIDER=deterministic\n")
	if err := os.WriteFile(path, oldContents, 0600); err != nil {
		t.Fatal(err)
	}
	settings := localconfig.NewSettings(path, console.ConfigStatus{Provider: "deterministic"}).
		WithOnChange(func(console.ConfigStatus) error { return errors.New("bad CA file") })
	if err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", BaseURL: "http://127.0.0.1:8000/v1", Model: "small"}); err == nil {
		t.Fatal("failed apply was reported as success")
	}
	if settings.Status().Provider != "deterministic" {
		t.Fatal("failed apply changed live status")
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(contents) != string(oldContents) {
		t.Fatalf("failed apply changed the saved config: %s", contents)
	}
}

func TestSwitchingEndpointNeverForwardsInheritedKey(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	settings := localconfig.NewSettings(path, console.ConfigStatus{Provider: "deterministic"})
	if err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", BaseURL: "https://cloud.example/v1", Model: "large", APIKey: "cloud-secret"}); err != nil {
		t.Fatal(err)
	}
	if err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", BaseURL: "http://127.0.0.1:8000/v1", Model: "small"}); err != nil {
		t.Fatal(err)
	}
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(contents), "cloud-secret") || settings.Status().HasAPIKey {
		t.Fatal("old cloud API key remained after switching to keyless local model")
	}
	if err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", BaseURL: "http://127.0.0.1:8000/v1", Model: "small", APIKey: "new-key"}); err != nil {
		t.Fatal(err)
	}
	if err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", BaseURL: "http://127.0.0.1:8000/v1", Model: "small", ClearAPIKey: true}); err != nil {
		t.Fatal(err)
	}
	if settings.Status().HasAPIKey {
		t.Fatal("explicit key clear was ignored")
	}
}

func TestStageStatusesShowIndependentModelsWithoutSecrets(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	if err := os.WriteFile(path, []byte("AGENT_PROVIDER=deterministic\nAGENT_INTENT_PROVIDER=openai\nAGENT_INTENT_BASE_URL=http://127.0.0.1:8000/v1\nAGENT_INTENT_MODEL=quantized\nAGENT_PLANNING_PROVIDER=openai\nAGENT_PLANNING_BASE_URL=https://fleet.example/v1/assist\nAGENT_PLANNING_MODEL=cloud-planning\nAGENT_CLOUD_ASSIST_URL=https://fleet.example/v1/assist\nAGENT_CLOUD_ASSIST_DEVICE_TOKEN=secret\n"), 0600); err != nil {
		t.Fatal(err)
	}
	status := localconfig.NewSettings(path, console.ConfigStatus{}).Status()
	if status.Stages["intent"].Model != "quantized" || !status.Stages["planning"].UsesCloudAssist {
		t.Fatalf("stage routing was not shown: %+v", status.Stages)
	}
	encoded := strings.Join([]string{status.Stages["intent"].BaseURL, status.Stages["planning"].BaseURL, status.Stages["planning"].Model}, " ")
	if strings.Contains(encoded, "secret") {
		t.Fatal("device token escaped through model status")
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
		WithOnChange(func(console.ConfigStatus) error { applied++; return nil })

	// An OpenAI-compatible endpoint still needs a URL and a model.
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
