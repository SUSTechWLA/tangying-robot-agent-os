package main

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestRuntimeOptionsRejectAmbiguousDeploymentValues(t *testing.T) {
	for _, test := range []struct{ key, value string }{
		{"EDGE_RUNTIME_INSECURE", "sometimes"},
		{"EDGE_STARTUP_TIMEOUT", "forever"},
		{"EDGE_CONTROL_TIMEOUT", "0s"},
		{"EDGE_TELEMETRY_INTERVAL", "-1s"},
		{"EDGE_HEARTBEAT_INTERVAL", "1ns"},
		{"EDGE_LEASE", "1s"},
		{"EDGE_TASK_SOURCE", "htpt"},
		{"EDGE_WORLD_POSE", "1,2,3"},
		{"EDGE_WORLD_POSE", "1,2,3,4,5"},
		{"EDGE_WORLD_POSE", "1,2,NaN,4"},
		{"EDGE_WORLD_POSE", "1,2,3,+Inf"},
	} {
		t.Run(test.key+"="+test.value, func(t *testing.T) {
			t.Setenv(test.key, test.value)
			if _, err := readRuntimeOptions(); err == nil || !strings.Contains(err.Error(), test.key) {
				t.Fatalf("error=%v", err)
			}
		})
	}
}

func TestDurablePathsKeepLegacyRobotJournalAndPinAdjacent(t *testing.T) {
	t.Setenv("EDGE_EXECUTION_DB", "")
	t.Setenv("EDGE_RUNTIME_CONTRACT_FILE", "")
	journal, pin, err := durablePaths("robot-7")
	if err != nil || !filepath.IsAbs(journal) || pin != journal+".runtime-contract.json" {
		t.Fatalf("journal=%q pin=%q err=%v", journal, pin, err)
	}
	if !strings.Contains(journal, filepath.Join("artifacts", "edge-worker")) {
		t.Fatalf("legacy journal changed: %s", journal)
	}
	t.Setenv("EDGE_EXECUTION_DB", filepath.Join(t.TempDir(), "execution.db"))
	t.Setenv("EDGE_RUNTIME_CONTRACT_FILE", "")
	journal, pin, err = durablePaths("robot-7")
	if err != nil || pin != journal+".runtime-contract.json" {
		t.Fatalf("journal=%q pin=%q err=%v", journal, pin, err)
	}
	t.Setenv("EDGE_RUNTIME_CONTRACT_FILE", journal)
	if _, _, err := durablePaths("robot-7"); err == nil {
		t.Fatal("contract pin could overwrite journal")
	}
}

func TestValidDeploymentDurationsAndPoseRemainExplicit(t *testing.T) {
	t.Setenv("EDGE_WORLD_POSE", "1,2,3,0.5")
	t.Setenv("EDGE_HEARTBEAT_INTERVAL", "250ms")
	t.Setenv("EDGE_LEASE", "2s")
	if _, err := readRuntimeOptions(); err != nil {
		t.Fatal(err)
	}
	pose, err := parseFloats("1,2,3,0.5", 4)
	if err != nil || len(pose) != 4 || pose[3] != .5 {
		t.Fatalf("pose=%v err=%v", pose, err)
	}
}
