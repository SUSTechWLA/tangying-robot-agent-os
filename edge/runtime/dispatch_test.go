package runtime

import (
	"context"
	"reflect"
	"testing"
	"time"
)

func TestDispatchBudgetUsesCapabilityWithFallbackAndCeiling(t *testing.T) {
	now := time.Now()
	for _, tc := range []struct {
		name             string
		advertised, want time.Duration
	}{
		{"long navigation", 4 * time.Minute, 4 * time.Minute},
		{"shorter driver contract", 20 * time.Second, 20 * time.Second},
		{"legacy missing", 0, time.Minute},
		{"invalid negative", -time.Second, time.Minute},
		{"bounded oversized", 24 * time.Hour, 10 * time.Minute},
	} {
		t.Run(tc.name, func(t *testing.T) {
			planned := Command{Capability: CapabilityNavigate, CommandID: "immutable", IdempotencyKey: "immutable", ApprovalID: "approved", Lease: time.Minute, Deadline: now.Add(-time.Hour)}
			snapshot := Snapshot{Capabilities: []Capability{{Name: string(CapabilityNavigate), DefaultTimeout: tc.advertised}}}
			got := CommandAtDispatch(context.Background(), planned, snapshot, now)
			if got.Lease != tc.want || !got.Deadline.Equal(now.Add(tc.want)) {
				t.Fatalf("dispatch lease=%v deadline=%v", got.Lease, got.Deadline)
			}
			got.Lease, got.Deadline = planned.Lease, planned.Deadline
			if !reflect.DeepEqual(got, planned) {
				t.Fatal("dispatch changed immutable identity or approval")
			}
		})
	}
}

func TestDispatchBudgetPreservesExplicitParentDeadline(t *testing.T) {
	now := time.Now()
	ctx, cancel := context.WithDeadline(context.Background(), now.Add(7*time.Second))
	defer cancel()
	got := CommandAtDispatch(ctx, Command{Capability: CapabilityNavigate, Lease: time.Minute}, Snapshot{Capabilities: []Capability{{Name: string(CapabilityNavigate), DefaultTimeout: 4 * time.Minute}}}, now)
	if !got.Deadline.Equal(now.Add(7*time.Second)) || got.Lease != 7*time.Second {
		t.Fatalf("parent budget escaped: %#v", got)
	}
}

func TestDispatchBindsReadIdentityAndPreservesExplicitSelection(t *testing.T) {
	snapshot := Snapshot{RobotID: "gazebo-tabletop", CatalogRevision: "current"}
	command := Command{Capability: "observe_scene"}
	got := CommandAtDispatch(context.Background(), command, snapshot, time.Now())
	if got.RobotID != "" || got.CatalogRevision != snapshot.CatalogRevision {
		t.Fatalf("read command missing runtime identity: %#v", got)
	}
	command.RobotID, command.CatalogRevision = "other", "stale"
	got = CommandAtDispatch(context.Background(), command, snapshot, time.Now())
	if got.RobotID != "other" || got.CatalogRevision != "stale" {
		t.Fatal("dispatch silently rewrote explicit identity")
	}
}
