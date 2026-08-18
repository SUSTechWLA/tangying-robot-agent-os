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
	content := []byte("LOCAL_LISTEN=127.0.0.1:8787\nROBOT_ADDRESS=xlerobot.local:50051\nROBOT_SERVER_NAME=xlerobot.local\nROBOT_CA=/certs/ca.crt\nROBOT_CERT=/certs/client.crt\nROBOT_KEY=/certs/client.key\nAGENT_PROVIDER=openai\nAGENT_BASE_URL=https://llm.example/v1\nAGENT_API_KEY=secret\nAGENT_MODEL=robot-model\nAGENT_ORCHESTRATION_SAMPLES=3\n")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	config, err := parseConfig([]string{"--config", path})
	if err != nil {
		t.Fatal(err)
	}
	if config.listen != "127.0.0.1:8787" || config.robotAddress != "xlerobot.local:50051" {
		t.Fatalf("network config = %#v", config)
	}
	if config.robotCA != "/certs/ca.crt" || config.robotCert != "/certs/client.crt" || config.robotKey != "/certs/client.key" {
		t.Fatalf("TLS config = %#v", config)
	}
	if config.robotServerName != "xlerobot.local" || config.llmProvider != "openai" {
		t.Fatalf("runtime config = %#v", config)
	}
	if config.llmBaseURL != "https://llm.example/v1" || config.llmAPIKey != "secret" || config.llmModel != "robot-model" || config.llmSamples != 3 {
		t.Fatalf("LLM config = %#v", config)
	}
}

func TestParseConfigRejectsRemovedCloudURL(t *testing.T) {
	path := t.TempDir() + "/local.env"
	if err := os.WriteFile(path, []byte("CLOUD_URL=https://obsolete.example\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := parseConfig([]string{"--config", path}); err == nil {
		t.Fatal("obsolete CLOUD_URL was accepted")
	}
}
