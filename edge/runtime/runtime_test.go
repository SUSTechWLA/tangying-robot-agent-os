package runtime

import "testing"

func TestSnapshotCanExecuteRequiresAdvertisedAvailableCapability(t *testing.T) {
	snapshot := Snapshot{
		Ready: true,
		Capabilities: []Capability{
			{Name: "observe_scene", Available: true},
			{Name: "manipulation.pick", Available: false, Blockers: []string{"CALIBRATION_REQUIRED"}},
		},
	}
	if err := snapshot.CanExecute("observe_scene"); err != nil {
		t.Fatal(err)
	}
	if err := snapshot.CanExecute("manipulation.pick"); err == nil {
		t.Fatal("unavailable capability must be rejected")
	}
	if err := snapshot.CanExecute("shell.execute"); err == nil {
		t.Fatal("unknown capability must be rejected")
	}
}
