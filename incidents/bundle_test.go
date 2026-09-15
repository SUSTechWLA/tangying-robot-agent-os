package incidents_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/incidents"
)

func sample() incidents.Bundle {
	return incidents.Bundle{
		Task: incidents.Task{ID: "task-1", Request: "把红色杯子放进右侧收纳盒", Adapter: "mujoco",
			Revision: 1, State: "RECOVERABLE_FAILURE", TerminalCode: "skill verify_placement failed: PLACEMENT_NOT_OBSERVED",
			EndedAt: time.Unix(1_789_000_000, 0).UTC()},
		Recovery: incidents.Recovery{CanResume: true, ReasonCode: "RESUME_AVAILABLE",
			CompletedStepIDs: []string{"observe", "pick"}, UncertainStepIDs: []string{}},
		Environment: incidents.Environment{RobotID: "robot-1", SoftwareVersion: "0.6.0",
			CatalogRevision: "catalog-v1", MapID: "scan-1", MapRevision: "rev-1"},
		Timeline: []incidents.TimelineEvent{{Sequence: 1, Type: "TOOL_ACTIVITY", StepID: "verify_place",
			ToolName: "verify_placement", Status: "FAILED", Error: "PLACEMENT_NOT_OBSERVED"}},
		StepRuns: []incidents.StepRun{{StepID: "verify_place", Capability: "verify_placement",
			Status: "STARTED"}},
		Evidence: []incidents.Evidence{{ID: "cap-1", StepID: "verify_place", RGBSHA256: "aa"}},
	}
}

func TestABundleCarriesTheFactsAndSaysItChangedNothing(t *testing.T) {
	directory := t.TempDir()
	writer := incidents.New(directory)
	path, err := writer.Write(sample())
	if err != nil {
		t.Fatalf("write: %v", err)
	}
	if filepath.Dir(path) != directory || !strings.HasSuffix(path, "task-1.bundle.json") {
		t.Fatalf("path = %s", path)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if decoded["schemaVersion"] != incidents.SchemaVersion {
		t.Fatalf("schemaVersion = %v", decoded["schemaVersion"])
	}
	if !strings.Contains(string(raw), "PLACEMENT_NOT_OBSERVED") {
		t.Fatal("the bundle must carry the error the record is about")
	}
	// The record must state, in itself, that nothing was fixed automatically.
	note, _ := decoded["note"].(string)
	if !strings.Contains(note, "不修改任何东西") {
		t.Fatalf("note = %q", note)
	}
	// Timings are recorded verbatim or not at all - never invented.
	if decoded["timing"] != nil {
		t.Fatalf("timing = %v, want null when the deployment records none", decoded["timing"])
	}
	// Recovery is the first thing an operator asks about.
	recovery := decoded["recovery"].(map[string]any)
	if recovery["canResume"] != true || recovery["reasonCode"] != "RESUME_AVAILABLE" {
		t.Fatalf("recovery = %v", recovery)
	}
	environment := decoded["environment"].(map[string]any)
	if environment["catalogRevision"] != "catalog-v1" || environment["mapRevision"] != "rev-1" {
		t.Fatalf("environment fingerprint missing: %v", environment)
	}
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Fatal("the temporary file must not survive the write")
	}
}

func TestABundleNeedsATaskIdentityAndFillsItsOwnTimestamps(t *testing.T) {
	writer := incidents.New(t.TempDir())
	if _, err := writer.Write(incidents.Bundle{}); err == nil {
		t.Fatal("a bundle without a task id must be refused")
	}
	bundle := sample()
	bundle.CollectedAt = time.Time{}
	path, err := writer.Write(bundle)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	var decoded struct {
		CollectedAt time.Time `json:"collectedAt"`
	}
	_ = json.Unmarshal(raw, &decoded)
	if decoded.CollectedAt.IsZero() {
		t.Fatal("the bundle must stamp when it was collected")
	}
}

func TestEmptySlicesSerialiseAsListsSoReadersDoNotSpecialCaseNull(t *testing.T) {
	writer := incidents.New(t.TempDir())
	path, err := writer.Write(incidents.Bundle{Task: incidents.Task{ID: "task-empty", State: "FAILED_SAFE"}})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	for _, key := range []string{`"timeline": []`, `"stepRuns": []`, `"evidence": []`} {
		if !strings.Contains(string(raw), key) {
			t.Fatalf("missing %s in %s", key, raw)
		}
	}
}

func TestOldBundlesArePrunedSoAFleetDoesNotFillItsDisk(t *testing.T) {
	directory := t.TempDir()
	writer := incidents.New(directory)
	writer.Keep = 3
	clock := time.Unix(1_789_000_000, 0)
	for index := 0; index < 6; index++ {
		writer.Now = func() time.Time { return clock }
		bundle := sample()
		bundle.Task.ID = "task-" + string(rune('a'+index))
		if _, err := writer.Write(bundle); err != nil {
			t.Fatal(err)
		}
		// Modification time is what pruning orders by, so advance it explicitly.
		path := filepath.Join(directory, bundle.Task.ID+".bundle.json")
		if err := os.Chtimes(path, clock, clock); err != nil {
			t.Fatal(err)
		}
		clock = clock.Add(time.Minute)
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		t.Fatal(err)
	}
	bundles := 0
	for _, entry := range entries {
		if strings.HasSuffix(entry.Name(), ".bundle.json") {
			bundles++
		}
	}
	if bundles != 3 {
		t.Fatalf("kept %d bundles, want 3", bundles)
	}
	// The newest survive; the oldest are the ones removed.
	if _, err := os.Stat(filepath.Join(directory, "task-a.bundle.json")); !os.IsNotExist(err) {
		t.Fatal("the oldest bundle should have been pruned")
	}
	if _, err := os.Stat(filepath.Join(directory, "task-f.bundle.json")); err != nil {
		t.Fatal("the newest bundle must survive")
	}
}

func TestTheDigestDistinguishesDifferentFailuresAndMatchesIdenticalOnes(t *testing.T) {
	first := incidents.Digest(sample())
	if first == "" {
		t.Fatal("digest must not be empty")
	}
	if first != incidents.Digest(sample()) {
		t.Fatal("the same facts must produce the same digest")
	}
	other := sample()
	other.Timeline[0].Error = "GRASP_NOT_OBSERVED"
	if incidents.Digest(other) == first {
		t.Fatal("a different failure must produce a different digest")
	}
}
