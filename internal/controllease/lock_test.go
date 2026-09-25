package controllease

import "testing"

func TestOneControllerPerRobotOnHost(t *testing.T) {
	t.Setenv("ROBOT_CONTROL_LOCK_DIR", t.TempDir())
	first, err := Acquire("robot-7", "127.0.0.1:50051")
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := Acquire("robot-7", "127.0.0.1:50051"); err == nil {
		second.Close()
		t.Fatal("second controller acquired the same robot")
	}
	other, err := Acquire("robot-8", "127.0.0.1:50052")
	if err != nil {
		t.Fatalf("other robot was blocked: %v", err)
	}
	other.Close()
	// Independent simulations often report the same robot ID. Distinct Runtime
	// endpoints must be able to run side by side.
	parallel, err := Acquire("robot-7", "127.0.0.1:50053")
	if err != nil {
		t.Fatalf("independent Runtime was blocked: %v", err)
	}
	parallel.Close()
	first.Close()
	if replacement, err := Acquire("robot-7", "127.0.0.1:50051"); err != nil {
		t.Fatalf("handoff after first controller stopped failed: %v", err)
	} else {
		replacement.Close()
	}
}
