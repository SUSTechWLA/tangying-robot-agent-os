package main

import (
	"strings"
	"testing"
)

func TestProductionConfigFailsClosedBeforeStartup(t *testing.T) {
	values := map[string]string{
		"FLEET_PRODUCTION":         "1",
		"FLEET_OPERATOR_PASSWORD":  strings.Repeat("p", 24),
		"FLEET_AUTH_SECRET":        strings.Repeat("s", 32),
		"FLEET_DEVICE_CREDENTIALS": "robot-1:" + strings.Repeat("d", 32),
		"MYSQL_PASSWORD":           strings.Repeat("m", 24),
		"MYSQL_ROOT_PASSWORD":      strings.Repeat("r", 24),
	}
	getenv := func(key string) string { return values[key] }
	if err := validateProductionConfig("mysql", getenv); err != nil {
		t.Fatal(err)
	}
	for _, check := range []struct{ name, key, value string }{
		{"memory storage", "", ""},
		{"demo operator password", "FLEET_OPERATOR_PASSWORD", "admin123"},
		{"missing signing key", "FLEET_AUTH_SECRET", ""},
		{"short device token", "FLEET_DEVICE_CREDENTIALS", "robot-1:short"},
		{"short assist token", "FLEET_ASSIST_DEVICE_CREDENTIALS", "robot-1:short"},
		{"demo database password", "MYSQL_PASSWORD", "change-me"},
	} {
		t.Run(check.name, func(t *testing.T) {
			store := "mysql"
			if check.key == "" {
				store = "memory"
			} else {
				previous := values[check.key]
				values[check.key] = check.value
				defer func() { values[check.key] = previous }()
			}
			if err := validateProductionConfig(store, getenv); err == nil {
				t.Fatal("unsafe production configuration was accepted")
			}
		})
	}
	values["FLEET_PRODUCTION"] = ""
	if err := validateProductionConfig("memory", getenv); err != nil {
		t.Fatalf("isolated development should remain available: %v", err)
	}
}
