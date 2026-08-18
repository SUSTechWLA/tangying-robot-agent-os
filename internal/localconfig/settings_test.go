package localconfig

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
)

func TestUpdateLLMPreservesRobotConfigAndWritesPrivateFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	if err := os.WriteFile(path, []byte("ROBOT_ADDRESS=xlerobot.local:50051\nROBOT_CA=/certs/ca.crt\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	settings := NewSettings(path, console.ConfigStatus{})
	if err := settings.UpdateLLM(console.LLMConfig{
		Provider: "openai", BaseURL: "https://llm.example/v1", Model: "robot-model", APIKey: "secret-key",
	}); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)
	for _, expected := range []string{
		"ROBOT_ADDRESS=xlerobot.local:50051", "ROBOT_CA=/certs/ca.crt",
		"AGENT_PROVIDER=openai", "AGENT_BASE_URL=https://llm.example/v1",
		"AGENT_MODEL=robot-model", "AGENT_API_KEY=secret-key",
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("config missing %q: %s", expected, text)
		}
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("mode = %o", info.Mode().Perm())
	}
	status := settings.Status()
	if !status.HasAPIKey || status.Provider != "openai" || status.Model != "robot-model" || !status.RestartRequired {
		t.Fatalf("status = %#v", status)
	}
}

func TestUpdateLLMRejectsIncompleteOpenAIConfig(t *testing.T) {
	settings := NewSettings(filepath.Join(t.TempDir(), "local.env"), console.ConfigStatus{})
	err := settings.UpdateLLM(console.LLMConfig{Provider: "openai", Model: "robot-model"})
	if err == nil {
		t.Fatal("incomplete OpenAI configuration was accepted")
	}
}
