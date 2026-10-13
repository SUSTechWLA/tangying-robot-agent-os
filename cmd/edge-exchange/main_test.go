package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

func testConfig(t *testing.T) (config, string) {
	t.Helper()
	dir := t.TempDir()
	write := func(name, token string) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, []byte(token), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	c := config{Identity: x.Identity{AgentID: "edge-a", RobotID: "ugv-a", Domain: "warehouse"}, Database: filepath.Join(dir, "state", "exchange.db"), Capacity: 100, Listen: "127.0.0.1:9811", AdminListen: "127.0.0.1:9812", AdminTokenFile: write("admin", strings.Repeat("a", 32)), AllowLoopbackHTTP: true, Peers: []peerConfig{{Identity: x.Identity{AgentID: "cloud-b", RobotID: "fleet", Domain: "cloud"}, URL: "http://127.0.0.1:9821", IncomingTokenFile: write("incoming", strings.Repeat("b", 32)), OutgoingTokenFile: write("outgoing", strings.Repeat("c", 32))}}}
	return c, filepath.Join(dir, "exchange.json")
}

func saveConfig(t *testing.T, c config, path string) {
	t.Helper()
	raw, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestCheckConfigurationDoesNotCreateJournal(t *testing.T) {
	c, path := testConfig(t)
	saveConfig(t, c, path)
	if _, err := loadConfig(path); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Dir(c.Database)); !os.IsNotExist(err) {
		t.Fatal("configuration check created state directory")
	}
}

func TestRejectUnsafeDeploymentConfiguration(t *testing.T) {
	for name, mutate := range map[string]func(*config){
		"remote-admin":         func(c *config) { c.AdminListen = "0.0.0.0:9812" },
		"remote-http":          func(c *config) { c.Listen = "0.0.0.0:9811" },
		"remote-destination":   func(c *config) { c.Peers[0].URL = "http://192.0.2.1:9811" },
		"admin-token-leak":     func(c *config) { c.Peers[0].OutgoingTokenFile = c.AdminTokenFile },
		"peer-admin-confusion": func(c *config) { c.Peers[0].IncomingTokenFile = c.AdminTokenFile },
		"missing-tls":          func(c *config) { c.AllowLoopbackHTTP = false; c.Peers[0].URL = "https://edge-b.example" },
		"ambiguous-tls":        func(c *config) { c.TLSCertFile = "unused.pem" },
	} {
		t.Run(name, func(t *testing.T) {
			c, path := testConfig(t)
			mutate(&c)
			saveConfig(t, c, path)
			if _, err := loadConfig(path); err == nil {
				t.Fatal("unsafe configuration accepted")
			}
		})
	}
}

func TestRejectWorldReadableSecrets(t *testing.T) {
	c, path := testConfig(t)
	if err := os.Chmod(c.AdminTokenFile, 0o644); err != nil {
		t.Fatal(err)
	}
	saveConfig(t, c, path)
	if _, err := loadConfig(path); err == nil {
		t.Fatal("public secret accepted")
	}
}
