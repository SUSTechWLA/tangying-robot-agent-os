package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

func TestLocalConfigWiresOutputCapsAndAccountingIntoBothDeciders(t *testing.T) {
	dir := t.TempDir()
	usage := filepath.Join(dir, "calls.jsonl")
	path := filepath.Join(dir, "agent.env")
	content := "LOCAL_ROBOT_ID=robot-budget\nAGENT_PROVIDER=deterministic\nAGENT_MODEL_USAGE_FILE=" + usage + "\n" +
		"AGENT_GOAL_PROVIDER=openai\nAGENT_GOAL_BASE_URL=http://127.0.0.1:8000/v1\nAGENT_GOAL_MODEL=local\nAGENT_GOAL_OUTPUT_TOKENS=1024\n" +
		"AGENT_RECOVERY_PROVIDER=openai\nAGENT_RECOVERY_BASE_URL=http://127.0.0.1:8000/v1\nAGENT_RECOVERY_MODEL=local\nAGENT_RECOVERY_OUTPUT_TOKENS=512\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	c, err := parseConfig([]string{"--config", path})
	if err != nil {
		t.Fatal(err)
	}
	planner, err := c.goalPlanner(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	recovery, err := c.recoveryDecider()
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		decider actionloop.Decider
		stage   string
		cap     int
	}{
		{planner.Decider, modelroute.Goal, 1024}, {recovery, modelroute.Recovery, 512},
	} {
		d, ok := tc.decider.(*actionloop.LLMDecider)
		if !ok || d.Stage != tc.stage || d.OutputTokens != tc.cap || d.UsageFile != usage {
			t.Fatalf("unwired accounting route: %+v", d)
		}
	}
}
