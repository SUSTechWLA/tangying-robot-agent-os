package runtime

import (
	"context"
	"errors"
	"testing"
)

type recordingClient struct {
	lastCommand Command
	info        Snapshot
}

func (c *recordingClient) Invoke(_ context.Context, command Command) (Result, error) {
	c.lastCommand = command
	return Result{Success: true}, nil
}

func (c *recordingClient) Info(context.Context) (Snapshot, error) {
	return c.info, nil
}

func (c *recordingClient) Cancel(context.Context, string, string) (bool, error) {
	return true, nil
}

func (c *recordingClient) EmergencyStop(context.Context, string) error {
	return nil
}

func TestRouterRoutesCommandToSelectedRobot(t *testing.T) {
	robotA := &recordingClient{info: Snapshot{RobotID: "robot-a"}}
	robotB := &recordingClient{info: Snapshot{RobotID: "robot-b"}}
	router := NewRouter("robot-a", robotA)
	if err := router.Register("robot-b", robotB); err != nil {
		t.Fatal(err)
	}
	if _, err := router.Invoke(context.Background(), Command{Capability: CapabilityPick, RobotID: "robot-b"}); err != nil {
		t.Fatal(err)
	}
	if robotB.lastCommand.Capability != CapabilityPick {
		t.Fatalf("robot B command = %+v", robotB.lastCommand)
	}
	if robotA.lastCommand.Capability != "" {
		t.Fatalf("robot A should not receive command, got %+v", robotA.lastCommand)
	}
}

func TestRouterFailsClosedForUnknownRobot(t *testing.T) {
	router := NewRouter("robot-a", &recordingClient{})
	_, err := router.Invoke(context.Background(), Command{RobotID: "robot-missing"})
	if !errors.Is(err, ErrRobotUnknown) {
		t.Fatalf("error = %v, want ErrRobotUnknown", err)
	}
}
