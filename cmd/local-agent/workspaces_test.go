package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWorkspacesConfigurationIsValidatedBeforeConnectingToRuntime(t *testing.T) {
	directory := t.TempDir()
	configPath := filepath.Join(directory, "local.env")
	registryPath := filepath.Join(directory, "workspaces.json")
	if err := os.WriteFile(registryPath, []byte(`{"schemaVersion":"edge.workspaces.v1","workspaces":[{"id":"ugv","label":"无人车","robotId":"robot-ugv","url":"http://127.0.0.1:8788","sessionFile":"/private/remote-console-session"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, []byte("LOCAL_ROBOT_ID=robot-root\nLOCAL_WORKSPACES_FILE="+registryPath+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	configuration, err := parseConfig([]string{"--config", configPath})
	if err != nil || configuration.workspaces == nil || configuration.workspacesFile != registryPath {
		t.Fatalf("registry config not loaded: config=%#v err=%v", configuration, err)
	}
	if _, err := parseConfig([]string{"--config", configPath, "--robot-id", "robot-ugv"}); err == nil {
		t.Fatal("remote alias of self accepted")
	}
	if _, err := parseConfig([]string{"--config", configPath, "--workspaces-file", filepath.Join(directory, "missing.json")}); err == nil {
		t.Fatal("missing explicit registry accepted")
	}
	if err := os.WriteFile(registryPath, []byte(`{"schemaVersion":"edge.workspaces.v1","workspaces":[{"id":"ugv","label":"无人车","robotId":"robot-ugv","url":"http://192.168.1.2:8788","sessionFile":"/private/remote-console-session"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := parseConfig([]string{"--config", configPath}); err == nil {
		t.Fatal("unsafe remote configuration accepted")
	}
}

func TestWorkspacesEnvironmentAndFlagOverride(t *testing.T) {
	directory := t.TempDir()
	registryPath := filepath.Join(directory, "workspaces.json")
	if err := os.WriteFile(registryPath, []byte(`{"schemaVersion":"edge.workspaces.v1","workspaces":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LOCAL_WORKSPACES_FILE", registryPath)
	configuration, err := parseConfig([]string{"--config", filepath.Join(directory, "missing.env")})
	if err != nil || configuration.workspaces == nil {
		t.Fatalf("environment registry not loaded: %v", err)
	}
	configuration, err = parseConfig([]string{"--config", filepath.Join(directory, "missing.env"), "--workspaces-file="})
	if err != nil || configuration.workspaces != nil {
		t.Fatalf("explicit empty flag did not disable registry: %v", err)
	}
}
