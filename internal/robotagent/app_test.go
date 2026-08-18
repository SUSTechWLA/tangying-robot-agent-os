package robotagent

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type recordedCommand struct {
	Name string
	Args []string
}

type recordingRunner struct {
	commands []recordedCommand
}

func (r *recordingRunner) Run(_ context.Context, name string, args ...string) error {
	r.commands = append(r.commands, recordedCommand{Name: name, Args: append([]string(nil), args...)})
	return nil
}

func newTestApp(t *testing.T, role string) (*App, *recordingRunner, *bytes.Buffer) {
	t.Helper()
	stateDir := t.TempDir()
	receipt := Receipt{Role: role, Version: "v0.1.0-rc.2", Commit: "abc123", OS: "linux", Arch: "amd64"}
	encoded, err := json.Marshal(receipt)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stateDir, "install.json"), encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &recordingRunner{}
	output := &bytes.Buffer{}
	app := &App{
		Version:   "v0.1.0-rc.2",
		RootDir:   "/opt/tangying-robot-agent-os",
		StateDir:  stateDir,
		ConfigDir: "/etc/tangying-robot-agent-os",
		Platform:  "linux",
		Runner:    runner,
		Stdout:    output,
		Stderr:    output,
	}
	return app, runner, output
}

func TestHelpAdvertisesStableCommands(t *testing.T) {
	app, _, output := newTestApp(t, "sim")
	if err := app.Run(context.Background(), []string{"help"}); err != nil {
		t.Fatal(err)
	}
	for _, command := range []string{"doctor", "configure", "pair", "start", "stop", "restart", "status", "logs", "demo", "version"} {
		if !strings.Contains(output.String(), command) {
			t.Fatalf("help does not contain %q:\n%s", command, output.String())
		}
	}
}

func TestVersionPrintsBuildAndInstallVersions(t *testing.T) {
	app, _, output := newTestApp(t, "local")
	if err := app.Run(context.Background(), []string{"version"}); err != nil {
		t.Fatal(err)
	}
	if got := output.String(); !strings.Contains(got, "cli=v0.1.0-rc.2") || !strings.Contains(got, "installed=v0.1.0-rc.2") {
		t.Fatalf("version output = %q", got)
	}
}

func TestLifecycleUsesRoleFromReceipt(t *testing.T) {
	app, runner, _ := newTestApp(t, "local")
	if err := app.Run(context.Background(), []string{"start"}); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{Name: "systemctl", Args: []string{"--user", "start", "tangying-robot-local-agent.service"}}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestExplicitRoleOverridesReceipt(t *testing.T) {
	app, runner, _ := newTestApp(t, "local")
	if err := app.Run(context.Background(), []string{"status", "cloud"}); err != nil {
		t.Fatal(err)
	}
	if len(runner.commands) != 1 || runner.commands[0].Name != "docker" {
		t.Fatalf("commands = %#v", runner.commands)
	}
	if got := strings.Join(runner.commands[0].Args, " "); !strings.Contains(got, "compose") || !strings.Contains(got, "ps") {
		t.Fatalf("docker args = %q", got)
	}
}

func TestLogsFollowIsPassedAsAllowlistedServiceArgument(t *testing.T) {
	app, runner, _ := newTestApp(t, "robot-pi")
	if err := app.Run(context.Background(), []string{"logs", "--follow"}); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{Name: "journalctl", Args: []string{"-u", "tangying-robot-edge.service", "-f"}}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestStopDoesNotParseRoleConfiguration(t *testing.T) {
	app, runner, _ := newTestApp(t, "local")
	app.ConfigDir = t.TempDir()
	if err := os.WriteFile(filepath.Join(app.ConfigDir, "local.env"), []byte("not valid configuration"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := app.Run(context.Background(), []string{"stop"}); err != nil {
		t.Fatal(err)
	}
	if len(runner.commands) != 1 || runner.commands[0].Args[2] != "tangying-robot-local-agent.service" {
		t.Fatalf("commands = %#v", runner.commands)
	}
}

func TestUnknownCommandAndRoleAreRejected(t *testing.T) {
	app, _, _ := newTestApp(t, "local")
	if err := app.Run(context.Background(), []string{"shell", "rm"}); err == nil || !strings.Contains(err.Error(), "unknown command") {
		t.Fatalf("unknown command error = %v", err)
	}
	if err := app.Run(context.Background(), []string{"start", "drone"}); err == nil || !strings.Contains(err.Error(), "unknown role") {
		t.Fatalf("unknown role error = %v", err)
	}
}

func TestConfigureWritesOnlyAllowedKeysWithPrivatePermissions(t *testing.T) {
	app, _, _ := newTestApp(t, "local")
	app.ConfigDir = t.TempDir()
	if err := app.Run(context.Background(), []string{
		"configure", "local", "CLOUD_URL=https://cloud.example", "ROBOT_ADDRESS=xlerobot.local:50051",
	}); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(app.ConfigDir, "local.env")
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := string(content); !strings.Contains(got, "CLOUD_URL=https://cloud.example") || !strings.Contains(got, "ROBOT_ADDRESS=xlerobot.local:50051") {
		t.Fatalf("config = %q", got)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("config permissions = %o", info.Mode().Perm())
	}
	if err := app.Run(context.Background(), []string{"configure", "local", "SHELL_COMMAND=rm -rf"}); err == nil {
		t.Fatal("unknown configuration key must be rejected")
	}
}

func TestDoctorRejectsLifecycleOnlyOptionsWithoutRunningCommands(t *testing.T) {
	app, runner, _ := newTestApp(t, "sim")
	err := app.Run(context.Background(), []string{"doctor", "--follow"})
	if err == nil || !strings.Contains(err.Error(), "unknown doctor option") {
		t.Fatalf("doctor error = %v", err)
	}
	if len(runner.commands) != 0 {
		t.Fatalf("doctor invoked commands: %#v", runner.commands)
	}
}

func TestRobotPiDoctorRunsCommittedNoMotionPreflight(t *testing.T) {
	app, runner, _ := newTestApp(t, "robot-pi")
	app.ConfigDir = t.TempDir()
	if err := os.WriteFile(filepath.Join(app.ConfigDir, "robot-pi.env"), []byte("ROBOT_GRPC_LISTEN=0.0.0.0:50051\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := app.Run(context.Background(), []string{"doctor", "robot-pi"}); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{
		Name: "bash",
		Args: []string{filepath.Join(app.RootDir, "scripts", "robot-pi-preflight.sh"), filepath.Join(app.ConfigDir, "robot-pi.env")},
	}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestPairDispatchesCommittedScriptWithSeparateSSHArguments(t *testing.T) {
	app, runner, _ := newTestApp(t, "local")
	if err := app.Run(context.Background(), []string{"pair", "xlerobot.local", "--ssh-user", "robot-owner"}); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{
		Name: "bash",
		Args: []string{filepath.Join(app.RootDir, "scripts", "pair-robot.sh"), "xlerobot.local", "--ssh-user", "robot-owner"},
	}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestPairForwardsExplicitCARotationFlag(t *testing.T) {
	app, runner, _ := newTestApp(t, "local")
	if err := app.Run(context.Background(), []string{"pair", "xlerobot.local", "--new-ca"}); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{
		Name: "bash",
		Args: []string{filepath.Join(app.RootDir, "scripts", "pair-robot.sh"), "xlerobot.local", "--ssh-user", "ubuntu", "--new-ca"},
	}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestResolveDirectoriesPrefersUserInstallReceiptOnLinux(t *testing.T) {
	home := t.TempDir()
	state := filepath.Join(home, ".local", "share", "tangying-robot-agent-os")
	if err := os.MkdirAll(state, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, "install.json"), []byte(`{"role":"local"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	gotState, gotConfig := resolveDirectories("linux", home, "", "")
	if gotState != state {
		t.Fatalf("StateDir = %q, want %q", gotState, state)
	}
	wantConfig := filepath.Join(home, ".config", "tangying-robot-agent-os")
	if gotConfig != wantConfig {
		t.Fatalf("ConfigDir = %q, want %q", gotConfig, wantConfig)
	}
}

func TestResolveRootFindsRepositoryFromInstalledBinary(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "install.sh"), []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(root, "bin")
	if err := os.MkdirAll(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	executable := filepath.Join(bin, "robot-agent")
	if err := os.WriteFile(executable, []byte("binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	want, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	if got := resolveRoot("", executable, "linux"); got != want {
		t.Fatalf("root = %q, want %q", got, want)
	}
}

func TestProductionCheckDispatchesCommittedGoNoGoScript(t *testing.T) {
	app, runner, _ := newTestApp(t, "robot-pi")
	if err := app.Run(context.Background(), []string{"production-check"}); err != nil {
		t.Fatal(err)
	}
	want := []recordedCommand{{
		Name: filepath.Join(app.RootDir, ".venv", "bin", "python"),
		Args: []string{
			filepath.Join(app.RootDir, "scripts", "xlerobot_production_check.py"),
			filepath.Join(app.ConfigDir, "robot-pi.env"),
		},
	}}
	if !reflect.DeepEqual(runner.commands, want) {
		t.Fatalf("commands = %#v, want %#v", runner.commands, want)
	}
}

func TestProductionCheckRejectsNonRobotRoles(t *testing.T) {
	app, runner, _ := newTestApp(t, "local")
	err := app.Run(context.Background(), []string{"production-check"})
	if err == nil || !strings.Contains(err.Error(), "only defined for robot-pi") {
		t.Fatalf("error = %v", err)
	}
	if len(runner.commands) != 0 {
		t.Fatalf("commands = %#v", runner.commands)
	}
}
