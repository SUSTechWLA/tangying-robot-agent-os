package robotclient

import (
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

func pinSnapshot() runtime.Snapshot {
	return runtime.Snapshot{RobotID: "robot-a", Adapter: "plugin", AdapterVersion: "1", ProtocolVersion: "1.0",
		CatalogRevision: "catalog-1", Ready: true,
		RobotProfile: &robotcontract.Profile{ModelID: "vehicle", ActionLimits: map[string]robotcontract.ActionLimit{"speed": {Max: 1, Unit: "m/s"}}},
		Capabilities: []runtime.Capability{{Name: "navigation.navigate", MutatesWorld: true, Available: true, SafetyLevel: "physical_motion"}}}
}

func TestDurableContractPinRejectsChangedContractAcrossRestart(t *testing.T) {
	for _, field := range []string{"robot", "adapter", "adapter-version", "protocol", "catalog", "profile-limit", "capability", "safety-profile", "profile-removed"} {
		t.Run(field, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "runtime-contract.json")
			first := &Client{contractPinPath: path, profile: "field"}
			if err := first.acceptDurableContract(pinSnapshot()); err != nil {
				t.Fatal(err)
			}
			original, _ := os.ReadFile(path)
			second := &Client{contractPinPath: path, profile: "field"}
			s := pinSnapshot()
			switch field {
			case "robot":
				s.RobotID = "robot-b"
			case "adapter":
				s.Adapter = "another"
			case "adapter-version":
				s.AdapterVersion = "2"
			case "protocol":
				s.ProtocolVersion = "1.1"
			case "catalog":
				s.CatalogRevision = "catalog-2"
			case "profile-limit":
				s.RobotProfile.ActionLimits["speed"] = robotcontract.ActionLimit{Max: .5, Unit: "m/s"}
			case "profile-removed":
				s.RobotProfile = nil
			case "capability":
				s.Capabilities[0].MutatesWorld = false
			case "safety-profile":
				second.profile = "uncommissioned"
			}
			if err := second.acceptDurableContract(s); !errors.Is(err, ErrContractChanged) {
				t.Fatalf("change accepted: %v", err)
			}
			after, _ := os.ReadFile(path)
			if string(original) != string(after) {
				t.Fatal("pin rewritten on mismatch")
			}
		})
	}
}

func TestDurableContractPinIgnoresTransientReadinessAndSurvivesRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "runtime-contract.json")
	if err := (&Client{contractPinPath: path}).acceptDurableContract(pinSnapshot()); err != nil {
		t.Fatal(err)
	}
	s := pinSnapshot()
	s.Ready = false
	s.Blockers = []string{"offline"}
	s.Capabilities[0].Available = false
	s.Capabilities[0].Blockers = []string{"localizing"}
	if err := (&Client{contractPinPath: path}).acceptDurableContract(s); err != nil {
		t.Fatal(err)
	}
}

func TestDurableContractPinRejectsMissingCorruptOrUnsafeStorage(t *testing.T) {
	for _, kind := range []string{"corrupt", "trailing", "oversized", "directory", "symlink", "disappeared", "relative", "wrong-robot", "missing-catalog"} {
		t.Run(kind, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "pin.json")
			client := &Client{contractPinPath: path}
			s := pinSnapshot()
			switch kind {
			case "corrupt":
				_ = os.WriteFile(path, []byte("{"), 0600)
			case "trailing":
				if err := client.acceptDurableContract(s); err != nil {
					t.Fatal(err)
				}
				f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
				_, _ = f.WriteString("{}")
				_ = f.Close()
			case "oversized":
				_ = os.WriteFile(path, make([]byte, 16385), 0600)
			case "directory":
				_ = os.Mkdir(path, 0700)
			case "symlink":
				_ = os.Symlink(filepath.Join(t.TempDir(), "absent"), path)
			case "disappeared":
				if err := client.acceptDurableContract(s); err != nil {
					t.Fatal(err)
				}
				_ = os.Remove(path)
			case "relative":
				client.contractPinPath = "relative-pin.json"
			case "wrong-robot":
				client.expectedRobotID = "robot-b"
			case "missing-catalog":
				s.CatalogRevision = ""
			}
			if err := client.acceptDurableContract(s); !errors.Is(err, ErrContractChanged) {
				t.Fatalf("unsafe storage admitted: %v", err)
			}
		})
	}
}

func TestDurableContractPinConcurrentAdmissionsCannotReplaceEachOther(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pin.json")
	var wg sync.WaitGroup
	outcomes := make(chan error, 2)
	for _, id := range []string{"robot-a", "robot-b"} {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			s := pinSnapshot()
			s.RobotID = id
			outcomes <- (&Client{contractPinPath: path}).acceptDurableContract(s)
		}(id)
	}
	wg.Wait()
	close(outcomes)
	successes := 0
	for err := range outcomes {
		if err == nil {
			successes++
		} else if !errors.Is(err, ErrContractChanged) {
			t.Fatal(err)
		}
	}
	if successes != 1 {
		t.Fatalf("admitted %d conflicting identities", successes)
	}
}

func TestPinnedInvokeRechecksContractWithFullyBoundCommand(t *testing.T) {
	client, server := strictScene(t)
	client.profile = "field"
	server.info.CatalogRevision = "original"
	client.contractPinPath = filepath.Join(t.TempDir(), "pin.json")
	if _, err := client.Info(t.Context()); err != nil {
		t.Fatal(err)
	}
	server.info.CatalogRevision = "changed"
	server.commands = make(chan *robotv1.SkillCommand, 1)
	_, err := client.Invoke(t.Context(), runtime.Command{RobotID: "robot-a", CatalogRevision: "original", SafetyProfile: "field", CommandID: "must-not-move", Deadline: time.Now().Add(time.Second)})
	if !errors.Is(err, ErrContractChanged) {
		t.Fatalf("fully bound command bypassed durable admission: %v", err)
	}
	if len(server.commands) != 0 {
		t.Fatal("rejected command reached Runtime")
	}
}

func TestPinnedInvokeRejectsSafetyProfileOverrideBeforeRPC(t *testing.T) {
	client := &Client{contractPinPath: filepath.Join(t.TempDir(), "pin.json"), profile: "commissioned"}
	_, err := client.Invoke(t.Context(), runtime.Command{SafetyProfile: "simulation"})
	if !errors.Is(err, ErrContractChanged) {
		t.Fatalf("override accepted: %v", err)
	}
}

func TestLostContractPinAfterRestartCannotSilentlyReadmitChangedRuntime(t *testing.T) {
	dir := t.TempDir()
	path, journal := filepath.Join(dir, "pin.json"), filepath.Join(dir, "agent.db")
	first := &Client{contractPinPath: path, contractHistoryPath: journal}
	if err := first.acceptDurableContract(pinSnapshot()); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(journal, []byte("existing durable execution history"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	second := &Client{contractPinPath: path, contractHistoryPath: journal}
	changed := pinSnapshot()
	changed.CatalogRevision = "new-controller"
	if err := second.acceptDurableContract(changed); !errors.Is(err, ErrContractChanged) {
		t.Fatalf("lost pin silently admitted: %v", err)
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("lost pin replaced")
	}
	// Explicit migration admits a missing record; it never overwrites one.
	second.admitMissingContract = true
	if err := second.acceptDurableContract(changed); err != nil {
		t.Fatal(err)
	}
	if err := second.acceptDurableContract(pinSnapshot()); !errors.Is(err, ErrContractChanged) {
		t.Fatalf("explicit admission overwrote existing pin: %v", err)
	}
}
