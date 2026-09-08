package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRobotSafetyProfileRequiresExplicitConfiguration(t *testing.T) {
	for _, arguments := range [][]string{nil, {"--dev-insecure"}, {"--robot", "robot.local:50051"}} {
		configuration, err := parseConfig(arguments)
		if err != nil {
			t.Fatal(err)
		}
		if configuration.robotSafetyProfile != "" {
			t.Fatalf("implicit safety profile for %v = %q; runtime policy must choose the default", arguments, configuration.robotSafetyProfile)
		}
	}
}

func TestRobotSafetyProfileFileAndFlagPrecedence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "local.env")
	if err := os.WriteFile(path, []byte("ROBOT_SAFETY_PROFILE=simulation\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name      string
		arguments []string
		want      string
	}{
		{"config file", []string{"--config", path}, "simulation"},
		{"explicit commissioned robot", []string{"--config", path, "--robot-safety-profile", "desktop"}, "desktop"},
		{"restore runtime default", []string{"--config=" + path, "--robot-safety-profile="}, ""},
		{"simulation flag", []string{"--robot-safety-profile=simulation"}, "simulation"},
	} {
		t.Run(test.name, func(t *testing.T) {
			configuration, err := parseConfig(test.arguments)
			if err != nil {
				t.Fatal(err)
			}
			if configuration.robotSafetyProfile != test.want {
				t.Fatalf("safety profile = %q, want %q", configuration.robotSafetyProfile, test.want)
			}
		})
	}
}
