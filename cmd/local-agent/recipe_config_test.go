package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localconfig"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

func TestRecipeModeFileOverridesEnvironmentAndRejectsUnknownMode(t *testing.T) {
	t.Setenv("AGENT_GOAL_RECIPE_MODE", "hint")
	p := filepath.Join(t.TempDir(), "local.env")
	if err := os.WriteFile(p, []byte("AGENT_GOAL_RECIPE_MODE=validated\n"), 0600); err != nil {
		t.Fatal(err)
	}
	c, err := parseConfig([]string{"--config", p})
	if err != nil || c.recipeMode != "validated" {
		t.Fatalf("mode=%q err=%v", c.recipeMode, err)
	}
	if err := os.WriteFile(p, []byte("AGENT_GOAL_RECIPE_MODE=unvalidated\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := parseConfig([]string{"--config", p}); err == nil {
		t.Fatal("unsafe recipe mode accepted")
	}
}

func TestRecipeModeHotReloadAppliesOffAndRejectsInvalidModeBeforeReplacingPlanner(t *testing.T) {
	for _, mode := range []string{"off", "invalid-mode", "environment-fallback"} {
		t.Run(mode, func(t *testing.T) {
			t.Setenv("AGENT_GOAL_RECIPE_MODE", "hint")
			path := filepath.Join(t.TempDir(), "local.env")
			base := "AGENT_PROVIDER=deterministic\nAGENT_GOAL_PROVIDER=openai\nAGENT_GOAL_BASE_URL=http://127.0.0.1:1/v1\nAGENT_GOAL_MODEL=initial-model\n"
			if err := os.WriteFile(path, []byte(base+"AGENT_GOAL_RECIPE_MODE=validated\n"), 0600); err != nil {
				t.Fatal(err)
			}
			configuration, err := parseConfig([]string{"--config", path})
			if err != nil {
				t.Fatal(err)
			}
			store := eventlog.NewMemoryStore()
			var running *capabilityagent.Planner
			applied := 0
			// This is the same preparation/commit boundary used by the running
			// Console callback. Construction does not contact a model or robot.
			reload := func(console.ConfigStatus) error {
				values, err := readConfigFile(path)
				if err != nil {
					return err
				}
				return configuration.applyReloadedModels(values, nil, store, func(_ intent.Parser, _ orchestration.Planner, _ actionloop.Decider, next *capabilityagent.Planner) {
					applied++
					running = next
				})
			}
			if err := reload(console.ConfigStatus{}); err != nil || running == nil || running.Memory == nil || running.Memory.Mode != "validated" {
				t.Fatalf("initial validated planner unavailable: %+v %v", running, err)
			}
			previous := running
			settings := localconfig.NewSettings(path, console.ConfigStatus{}).WithOnChange(reload)
			newContent := base
			if mode != "environment-fallback" {
				newContent += "AGENT_GOAL_RECIPE_MODE=" + mode + "\n"
			}
			if err := os.WriteFile(path, []byte(newContent), 0600); err != nil {
				t.Fatal(err)
			}
			err = settings.UpdateLLM(console.LLMConfig{Stage: "GOAL", Provider: "openai", BaseURL: "http://127.0.0.1:1/v1", Model: "replacement-model"})
			if mode == "invalid-mode" {
				if err == nil || applied != 1 || running != previous || running.Memory.Mode != "validated" || running.Decider.(*actionloop.LLMDecider).Model != "initial-model" {
					t.Fatalf("rejected configuration partially replaced running models: applied=%d err=%v", applied, err)
				}
				contents, readErr := os.ReadFile(path)
				if readErr != nil || !bytes.Equal(contents, []byte(newContent)) {
					t.Fatalf("failed model update did not restore previous file: %q %v", contents, readErr)
				}
				return
			}
			if err != nil || applied != 2 || running == previous || running.Decider.(*actionloop.LLMDecider).Model != "replacement-model" || settings.Status().RestartRequired {
				t.Fatalf("valid model reload not applied: applied=%d err=%v", applied, err)
			}
			if mode == "off" && running.Memory != nil {
				t.Fatal("off reload retained the startup validated recipe mode")
			}
			if mode == "environment-fallback" && (running.Memory == nil || running.Memory.Mode != "hint") {
				t.Fatal("reload did not use the same environment fallback as startup")
			}
		})
	}
}
