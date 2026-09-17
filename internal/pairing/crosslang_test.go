package pairing_test

import (
	"bufio"
	"context"
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/pairing"
)

// The integration the fixture cannot cover.
//
// The fixture proves both sides agree on a key and a ciphertext. This proves the
// two implementations can actually complete a pairing: framing, ordering,
// timeouts, and the certificate written to disk — each done by its own code in its
// own language.
func TestTheAgentPairsWithTheRealPythonRobot(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatalf("resolve root: %v", err)
	}
	work := t.TempDir()
	command := exec.Command(filepath.Join(root, ".venv/bin/python"), "/tmp/python_robot_pair_server.py",
		"xlerobot-crosstest", filepath.Join(work, "state"), filepath.Join(work, "certs"))
	command.Dir = root
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatalf("stdout pipe: %v", err)
	}
	command.Stderr = command.Stdout
	if err := command.Start(); err != nil {
		t.Fatalf("start the robot: %v", err)
	}
	defer func() { _ = command.Process.Kill(); _, _ = command.Process.Wait() }()

	// The robot reports events before it reports readiness, because the window
	// opening *is* an event. Reading one line and assuming it is the readiness
	// line is the mistake this loop exists to avoid.
	reader := bufio.NewReader(stdout)
	var ready struct {
		Ready bool `json:"ready"`
		Port  int  `json:"port"`
		Code  string
	}
	var events []string
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		line, readErr := reader.ReadString('\n')
		if line != "" {
			var record struct {
				Event string `json:"event"`
				Ready *bool  `json:"ready"`
			}
			if err := json.Unmarshal([]byte(line), &record); err == nil {
				if record.Event != "" {
					events = append(events, record.Event)
				}
				if record.Ready != nil {
					_ = json.Unmarshal([]byte(line), &ready)
					break
				}
			}
		}
		if readErr != nil {
			t.Fatalf("the robot did not report readiness: %v (events so far: %v)", readErr, events)
		}
	}
	if !ready.Ready || ready.Port == 0 || ready.Code == "" {
		t.Fatalf("the robot did not open a pairing window: %+v (events: %v)", ready, events)
	}

	authorityDirectory := t.TempDir()
	authority, err := pairing.LoadOrCreateAuthority(
		filepath.Join(authorityDirectory, "ca.crt"), filepath.Join(authorityDirectory, "ca.key"))
	if err != nil {
		t.Fatalf("authority: %v", err)
	}
	client := &pairing.Client{Port: ready.Port, Timeout: 20 * time.Second}
	result, err := client.Pair(context.Background(), ready.Code, pairing.Address{
		RobotID: "xlerobot-crosstest", Host: "127.0.0.1", IP: "127.0.0.1", Authority: authority,
	})
	if err != nil {
		t.Fatalf("the Go agent could not pair with the Python robot: %v", err)
	}
	if result.RobotID != "xlerobot-crosstest" {
		t.Fatalf("robot id = %q", result.RobotID)
	}

	// The robot must have written the material, in the shape the SSH path writes.
	for name := range map[string]bool{"server.key": true, "server.crt": true, "client-ca.crt": true} {
		path := filepath.Join(work, "certs", name)
		if _, statErr := exec.Command("test", "-s", path).Output(); statErr != nil {
			t.Fatalf("the robot did not write %s", path)
		}
	}

	// And the robot's own audit trail must record the pairing. An owner has to be
	// able to see that a pairing happened, and to whom.
	paired := false
	for time.Now().Before(deadline) && !paired {
		line, readErr := reader.ReadString('\n')
		if line != "" && strings.Contains(line, "pairing.paired") {
			paired = true
		}
		if readErr != nil {
			break
		}
	}
	if !paired {
		t.Fatalf("the robot never reported the pairing (events so far: %v)", events)
	}
}
