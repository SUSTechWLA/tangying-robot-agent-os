package main

import (
	"strings"
	"testing"
)

// The console's listen address decides who can reach a robot's controls, and it
// used to decide nothing: the package doc said "loopback only" while the flag
// and the config key both accepted any address and no code checked.
//
// The rule now is that only a provably loopback address is served by default.
// These tests pin both halves: what counts as provably loopback, and that
// everything else needs the operator to say so out loud.

func TestOnlyAProvablyLoopbackAddressIsServedByDefault(t *testing.T) {
	for addr, want := range map[string]bool{
		"127.0.0.1:8787":  true,
		"127.0.0.1":       true,
		"localhost:8787":  true,
		"LocalHost:8787":  true,
		"[::1]:8787":      true,
		"127.5.5.5:8787":  true, // the whole 127/8 block is loopback
		":8787":           false,
		"0.0.0.0:8787":    false,
		"[::]:8787":       false,
		"192.168.1.10:87": false,
		"10.0.0.5:8787":   false,
		"robot.local:87":  false, // a hostname is not proof, and resolving it here would not be either
		"":                false,
	} {
		if got := loopbackListen(addr); got != want {
			t.Errorf("loopbackListen(%q) = %v, want %v", addr, got, want)
		}
	}
}

func TestARemoteBindIsRefusedUnlessAskedForByName(t *testing.T) {
	refusal := remoteConsoleRefusal("0.0.0.0:8787")
	message := refusal.Error()
	// The message has to name what is exposed, because the operator reading it is
	// usually about to do something reasonable for a reasonable reason ("I want
	// to watch it from my laptop") and needs to know the cost.
	for _, expected := range []string{"0.0.0.0:8787", "明文", "反向代理", "--allow-remote-console"} {
		if !strings.Contains(message, expected) {
			t.Errorf("refusal does not mention %q: %s", expected, message)
		}
	}
}

func TestTheOptInStillSaysWhatItExposes(t *testing.T) {
	warning := remoteConsoleWarning("0.0.0.0:8787")
	if !strings.Contains(warning, "0.0.0.0:8787") || !strings.Contains(warning, "警告") {
		t.Fatalf("opt-in warning = %q", warning)
	}
}

// parseConfig is the real gate, so the refusal is asserted there rather than only
// on the helper it calls.
func TestParseConfigRefusesARemoteBindWithoutTheFlag(t *testing.T) {
	if _, err := parseConfig([]string{"--listen", "0.0.0.0:8787"}); err == nil {
		t.Fatal("a wildcard bind was accepted without --allow-remote-console")
	}
	config, err := parseConfig([]string{"--listen", "0.0.0.0:8787", "--allow-remote-console"})
	if err != nil {
		t.Fatalf("explicit opt-in was refused: %v", err)
	}
	if config.listen != "0.0.0.0:8787" || !config.allowRemoteConsole {
		t.Fatalf("config = %+v", config)
	}
}

func TestParseConfigAcceptsTheDefaultLoopbackBind(t *testing.T) {
	config, err := parseConfig(nil)
	if err != nil {
		t.Fatalf("default config was refused: %v", err)
	}
	if !loopbackListen(config.listen) {
		t.Fatalf("default listen = %q, which is not loopback", config.listen)
	}
}
