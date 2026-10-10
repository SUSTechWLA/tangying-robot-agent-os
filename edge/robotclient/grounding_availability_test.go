package robotclient

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"
)

type groundingInfoSequence struct {
	robotv1.RobotRuntimeClient
	reads int
	value func(int) *robotv1.RuntimeInfo
}

func (p *groundingInfoSequence) GetRuntimeInfo(ctx context.Context, _ *robotv1.GetRuntimeInfoRequest, _ ...grpc.CallOption) (*robotv1.RuntimeInfo, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	p.reads++
	return p.value(p.reads), nil
}

func navigationAvailability(info *robotv1.RuntimeInfo, available bool) *robotv1.RuntimeInfo {
	copy := proto.Clone(info).(*robotv1.RuntimeInfo)
	for _, capability := range copy.Capabilities {
		if capability.Name == "navigation.navigate" {
			capability.Available = available
			if !available {
				capability.Blockers = []string{"JOINT_FEEDBACK_STALE"}
			}
		}
	}
	return copy
}

func TestGroundingWaitsForTransientNavigationReadinessBeforeSemanticObservation(t *testing.T) {
	for _, action := range []string{manipulation.ActionHomeRoute, manipulation.ActionHomeManipulation} {
		t.Run(string(action), func(t *testing.T) {
			client, server := householdSemanticFixture(t, "kitchen", "kitchen")
			server.requests = make(chan []string, 2)
			server.commands = make(chan *robotv1.SkillCommand, 1)
			sequence := &groundingInfoSequence{RobotRuntimeClient: client.robot, value: func(read int) *robotv1.RuntimeInfo {
				if len(server.requests) != 0 {
					t.Error("observed before readiness settled")
				}
				return navigationAvailability(server.info, read > 1)
			}}
			client.robot = sequence
			grounded, err := client.Ground(t.Context(), manipulation.Intent{Action: action, RouteRooms: []string{"living_room", "kitchen"},
				Object: manipulation.EntitySelector{Category: "cup"}, Destination: manipulation.EntitySelector{Category: "storage_bin"}})
			if err != nil {
				t.Fatal(err)
			}
			if sequence.reads != 2 || len(grounded.RouteGoals) != 2 || len(server.requests) == 0 {
				t.Fatalf("reads=%d goals=%v observations=%d", sequence.reads, grounded.RouteGoals, len(server.requests))
			}
			if len(server.commands) != 0 {
				t.Fatal("readiness refresh dispatched a physical command")
			}
		})
	}
}

func TestGroundingUnavailableCapabilityIsBoundedAndPreservesTheBlocker(t *testing.T) {
	client, server := householdSemanticFixture(t, "kitchen", "kitchen")
	server.requests = make(chan []string, 1)
	sequence := &groundingInfoSequence{RobotRuntimeClient: client.robot, value: func(int) *robotv1.RuntimeInfo { return navigationAvailability(server.info, false) }}
	client.robot = sequence
	ctx, cancel := context.WithTimeout(t.Context(), 4*time.Second)
	defer cancel()
	_, err := client.Ground(ctx, manipulation.Intent{Action: manipulation.ActionHomeRoute, RouteRooms: []string{"living_room"}})
	if !errors.Is(err, runtime.ErrCapabilityUnavailable) || !strings.Contains(err.Error(), "JOINT_FEEDBACK_STALE") {
		t.Fatalf("lost blocker or bounded timeout: %v", err)
	}
	if sequence.reads < 2 || len(server.requests) != 0 {
		t.Fatalf("reads=%d observations=%d", sequence.reads, len(server.requests))
	}
}

func TestGroundingReadinessCancellationDoesNotObserveOrDispatch(t *testing.T) {
	client, server := householdSemanticFixture(t, "kitchen", "kitchen")
	server.requests = make(chan []string, 1)
	server.commands = make(chan *robotv1.SkillCommand, 1)
	sequence := &groundingInfoSequence{RobotRuntimeClient: client.robot, value: func(int) *robotv1.RuntimeInfo { return navigationAvailability(server.info, false) }}
	client.robot = sequence
	ctx, cancel := context.WithTimeout(t.Context(), 20*time.Millisecond)
	defer cancel()
	_, err := client.Ground(ctx, manipulation.Intent{Action: manipulation.ActionHomeRoute, RouteRooms: []string{"living_room"}})
	if !errors.Is(err, context.DeadlineExceeded) || len(server.requests) != 0 || len(server.commands) != 0 {
		t.Fatalf("err=%v observations=%d commands=%d", err, len(server.requests), len(server.commands))
	}
}

func TestGroundingDoesNotWaitForAMissingNavigationCapability(t *testing.T) {
	client, server := strictScene(t)
	server.requests = make(chan []string, 1)
	sequence := &groundingInfoSequence{RobotRuntimeClient: client.robot, value: func(int) *robotv1.RuntimeInfo { return proto.Clone(server.info).(*robotv1.RuntimeInfo) }}
	client.robot = sequence
	_, err := client.Ground(t.Context(), manipulation.Intent{Action: manipulation.ActionHomeRoute, RouteRooms: []string{"living_room"}})
	if !errors.Is(err, runtime.ErrCapabilityUnknown) || sequence.reads != 1 || len(server.requests) != 0 {
		t.Fatalf("err=%v reads=%d observations=%d", err, sequence.reads, len(server.requests))
	}
}

func TestGroundingReadinessRejectsBindingChanges(t *testing.T) {
	for _, change := range []string{"robot", "protocol", "profile", "catalog"} {
		t.Run(change, func(t *testing.T) {
			client, server := householdSemanticFixture(t, "kitchen", "kitchen")
			server.requests = make(chan []string, 1)
			sequence := &groundingInfoSequence{RobotRuntimeClient: client.robot, value: func(read int) *robotv1.RuntimeInfo {
				info := navigationAvailability(server.info, read > 1)
				if read > 1 {
					switch change {
					case "robot":
						info.RobotId = "foreign-robot"
					case "protocol":
						info.ProtocolVersion = "2.0"
					case "profile":
						delete(info.RobotProfile.Fields, "tools")
					case "catalog":
						info.CatalogRevision = "changed-catalog"
					}
				}
				return info
			}}
			client.robot = sequence
			_, err := client.Ground(t.Context(), manipulation.Intent{Action: manipulation.ActionHomeRoute, RouteRooms: []string{"living_room"}})
			if err == nil || sequence.reads != 2 || len(server.requests) != 0 {
				t.Fatalf("accepted %s change: err=%v reads=%d observations=%d", change, err, sequence.reads, len(server.requests))
			}
		})
	}
}
