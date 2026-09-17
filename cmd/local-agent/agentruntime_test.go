package main

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
)

// The default deployment runs both agents. Changing that default changes what an
// operator gets without asking, so it is pinned.
func TestAgentRuntimeDefaultsToTaskAndOps(t *testing.T) {
	config := agentRuntimeConfig(nil)
	if len(config.Enabled) != 2 {
		t.Fatalf("default enabled = %#v, want task and ops", config.Enabled)
	}
	if !config.Requires(agentruntime.TaskAgentName) || !config.Requires(agentruntime.OpsAgentName) {
		t.Fatalf("default enabled = %#v", config.Enabled)
	}
	// An unset variable must behave exactly like the default.
	if fromEnv := agentRuntimeConfig(func(string) string { return "" }); len(fromEnv.Enabled) != 2 {
		t.Fatalf("unset TANGYING_AGENTS produced %#v", fromEnv.Enabled)
	}
}

// Turning the observer off is a supported deployment, not a test fixture: it is
// how an operator establishes whether an observation is changing behaviour.
func TestAgentRuntimeCanRunExecutionAlone(t *testing.T) {
	config := agentRuntimeConfig(func(key string) string {
		if key == "TANGYING_AGENTS" {
			return "task"
		}
		return ""
	})
	if config.Requires(agentruntime.OpsAgentName) {
		t.Fatalf("enabled = %#v, want the observer off", config.Enabled)
	}
	if !config.Requires(agentruntime.TaskAgentName) {
		t.Fatalf("enabled = %#v, want the executor on", config.Enabled)
	}
}

func TestAgentRuntimeParsesAList(t *testing.T) {
	config := agentRuntimeConfig(func(key string) string {
		if key == "TANGYING_AGENTS" {
			return " task , ops "
		}
		return ""
	})
	if len(config.Enabled) != 2 {
		t.Fatalf("enabled = %#v, want two trimmed names", config.Enabled)
	}
	for _, name := range config.Enabled {
		if name != "task" && name != "ops" {
			t.Fatalf("enabled = %#v contains an untrimmed name", config.Enabled)
		}
	}
}

// A value that names no agent is more likely a mistake than a request to run
// nothing, and running nothing looks identical to running fine.
func TestAgentRuntimeIgnoresAnEmptyAgentList(t *testing.T) {
	config := agentRuntimeConfig(func(key string) string {
		if key == "TANGYING_AGENTS" {
			return " , , "
		}
		return ""
	})
	if len(config.Enabled) == 0 {
		t.Fatal("an empty agent list silently disabled every agent")
	}
}

// "Abnormal" decides when an operator is told a task needs attention, so both
// directions of the mistake are pinned: a task the system could not finish must
// be reported, and an operator's own decision must not be.
func TestNeedsAttentionSeparatesFailureFromOperatorChoice(t *testing.T) {
	attention := []taskgraph.TaskState{
		taskgraph.StateFailed,
		taskgraph.StateRecoverableFailure,
		taskgraph.StateFailedSafe,
		taskgraph.StateSafetyStopped,
		taskgraph.StateWaitingUser,
		taskgraph.StateBlocked,
	}
	for _, state := range attention {
		if !needsAttention(state) {
			t.Errorf("%s must be reported to an operator", state)
		}
	}
	// CANCELLED was an operator's decision and PAUSED is a routine wait.
	// Reporting either would make the supervisor noisy, and a noisy supervisor is
	// one nobody reads. SUCCEEDED and in-flight states are obviously not failures.
	for _, state := range []taskgraph.TaskState{
		taskgraph.StateCancelled,
		taskgraph.StatePaused,
		taskgraph.StateSucceeded,
		taskgraph.StateReady,
		taskgraph.StateObserving,
		taskgraph.StatePlanning,
		taskgraph.StateExecuting,
		taskgraph.StateVerifying,
		taskgraph.StateWaitingApproval,
		taskgraph.StateWaitingForObservation,
		taskgraph.StateRecovering,
		taskgraph.StateSafeRecovery,
	} {
		if needsAttention(state) {
			t.Errorf("%s must not be reported as an abnormal ending", state)
		}
	}
}
