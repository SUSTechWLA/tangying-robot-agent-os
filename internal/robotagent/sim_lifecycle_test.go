package robotagent

import (
	"context"
	"path/filepath"
	"reflect"
	"testing"
)

func TestSimulationLifecycleUsesPersistentStackSupervisor(t *testing.T) {
	app, runner, _ := newTestApp(t, "sim")
	for _, operation := range []string{"start", "stop", "restart", "status"} {
		runner.commands = nil
		if err := app.Run(context.Background(), []string{operation, "sim"}); err != nil {
			t.Fatalf("%s: %v", operation, err)
		}
		want := []recordedCommand{{
			Name: "bash",
			Args: []string{filepath.Join(app.RootDir, "scripts", "sim-stack.sh"), operation},
		}}
		if !reflect.DeepEqual(runner.commands, want) {
			t.Fatalf("%s commands = %#v, want %#v", operation, runner.commands, want)
		}
	}
}

func TestSimulationLogsForwardsFollowToStackSupervisor(t *testing.T) {
	app, runner, _ := newTestApp(t, "sim")
	if err := app.Run(context.Background(), []string{"logs", "sim", "--follow"}); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{
		Name: "bash",
		Args: []string{filepath.Join(app.RootDir, "scripts", "sim-stack.sh"), "logs", "--follow"},
	}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestSimulationLifecycleForwardsAllowlistedStackOptions(t *testing.T) {
	app, runner, _ := newTestApp(t, "sim")
	arguments := []string{
		"start", "sim", "--foreground", "--sim-port", "50061", "--agent-port", "8788",
		"--artifacts-dir", "/tmp/tangying-sim-test", "--seed", "11",
	}
	if err := app.Run(context.Background(), arguments); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{
		Name: "bash",
		Args: []string{
			filepath.Join(app.RootDir, "scripts", "sim-stack.sh"), "start", "--foreground",
			"--sim-port", "50061", "--agent-port", "8788", "--artifacts-dir", "/tmp/tangying-sim-test", "--seed", "11",
		},
	}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestSimulationStackOptionsAreRejectedForPhysicalLifecycle(t *testing.T) {
	app, runner, _ := newTestApp(t, "robot-pi")
	err := app.Run(context.Background(), []string{"start", "robot-pi", "--sim-port", "50061"})
	if err == nil {
		t.Fatal("expected simulation-only option to be rejected")
	}
	if len(runner.commands) != 0 {
		t.Fatalf("commands = %#v", runner.commands)
	}
}
