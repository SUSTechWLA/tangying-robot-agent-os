package main

import (
	"os"
	"testing"
)

func TestParseConfigCarriesRobotMTLSFiles(t *testing.T) {
	config, err := parseConfig([]string{
		"--robot", "xlerobot.local:50051",
		"--robot-ca", "/etc/tangying/certs/ca.pem",
		"--robot-cert", "/etc/tangying/certs/mac.pem",
		"--robot-key", "/etc/tangying/certs/mac-key.pem",
		"--robot-server-name", "xlerobot.local",
	})
	if err != nil {
		t.Fatal(err)
	}
	if config.robotCA != "/etc/tangying/certs/ca.pem" {
		t.Fatalf("robotCA = %q", config.robotCA)
	}
	if config.robotCert != "/etc/tangying/certs/mac.pem" || config.robotKey != "/etc/tangying/certs/mac-key.pem" {
		t.Fatalf("client certificate files were not preserved: %#v", config)
	}
	if config.robotServerName != "xlerobot.local" {
		t.Fatalf("robotServerName = %q", config.robotServerName)
	}
}

func TestParseConfigLoadsKnownValuesFromFile(t *testing.T) {
	path := t.TempDir() + "/local.env"
	content := []byte("CLOUD_URL=https://cloud.example\nROBOT_ADDRESS=xlerobot.local:50051\nROBOT_SERVER_NAME=xlerobot.local\nROBOT_CA=/certs/ca.crt\nROBOT_CERT=/certs/client.crt\nROBOT_KEY=/certs/client.key\nAGENT_ID=laptop-7\n")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	config, err := parseConfig([]string{"--config", path})
	if err != nil {
		t.Fatal(err)
	}
	if config.cloudURL != "https://cloud.example" || config.robotAddress != "xlerobot.local:50051" {
		t.Fatalf("network config = %#v", config)
	}
	if config.robotCA != "/certs/ca.crt" || config.robotCert != "/certs/client.crt" || config.robotKey != "/certs/client.key" {
		t.Fatalf("TLS config = %#v", config)
	}
	if config.agentID != "laptop-7" || config.robotServerName != "xlerobot.local" {
		t.Fatalf("identity config = %#v", config)
	}
}
