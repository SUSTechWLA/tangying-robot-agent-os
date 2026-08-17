package main

import "testing"

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
