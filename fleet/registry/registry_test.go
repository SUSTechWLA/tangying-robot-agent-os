package registry

import (
	"context"
	"testing"
	"time"
)

func TestRegisterAndHeartbeatRenewLease(t *testing.T) {
	now := time.Now().UTC()
	registry := NewWithClock(NewMemoryStore(), func() time.Time { return now })
	ctx := context.Background()

	expiry, err := registry.Register(ctx, Device{RobotID: "robot-1", Adapter: "mujoco"}, 15*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if !expiry.Equal(now.Add(15 * time.Second)) {
		t.Fatalf("expiry = %v, want %v", expiry, now.Add(15*time.Second))
	}

	now = now.Add(10 * time.Second)
	expiry, err = registry.Heartbeat(ctx, "robot-1", 15*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if !expiry.Equal(now.Add(15 * time.Second)) {
		t.Fatalf("renewed expiry = %v, want %v", expiry, now.Add(15*time.Second))
	}

	device, ok, err := registry.Status(ctx, "robot-1")
	if err != nil || !ok {
		t.Fatalf("status = %+v (ok=%v, err=%v)", device, ok, err)
	}
	if !device.Online {
		t.Fatal("device must be online within lease")
	}
}

func TestDeviceGoesOfflineAfterLeaseExpiry(t *testing.T) {
	now := time.Now().UTC()
	registry := NewWithClock(NewMemoryStore(), func() time.Time { return now })
	ctx := context.Background()
	if _, err := registry.Register(ctx, Device{RobotID: "robot-1"}, 5*time.Second); err != nil {
		t.Fatal(err)
	}
	now = now.Add(6 * time.Second)
	device, ok, err := registry.Status(ctx, "robot-1")
	if err != nil || !ok {
		t.Fatalf("status err: %v", err)
	}
	if device.Online {
		t.Fatal("device must be offline after lease expiry")
	}
	list, err := registry.List(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 || list[0].Online {
		t.Fatalf("list = %+v, want one offline device", list)
	}
}

func TestUnknownDeviceStatus(t *testing.T) {
	registry := New(NewMemoryStore())
	_, ok, err := registry.Status(context.Background(), "ghost")
	if err != nil || ok {
		t.Fatalf("ghost device: ok=%v err=%v, want not found", ok, err)
	}
}

func TestRegistryKeepsToolAndObservationCatalogsSeparate(t *testing.T) {
	registry := New(NewMemoryStore())
	device := Device{
		RobotID: "robot-1", ToolCatalogRevision: "tool-rev", ObservationCatalogRevision: "obs-rev",
		ToolCatalog:        []ToolDescriptor{{Name: "manipulation.pick", SideEffectClass: "physical_atomic"}},
		ObservationSources: []ObservationSource{{SourceID: "robot-1/scene", TransformRevision: "scene-v1"}},
	}
	if _, err := registry.Register(context.Background(), device, time.Minute); err != nil {
		t.Fatal(err)
	}
	stored, ok, err := registry.Status(context.Background(), "robot-1")
	if err != nil || !ok {
		t.Fatalf("stored=%#v ok=%v err=%v", stored, ok, err)
	}
	if stored.ToolCatalogRevision != "tool-rev" || stored.ObservationCatalogRevision != "obs-rev" || stored.ToolCatalog[0].Name == stored.ObservationSources[0].SourceID {
		t.Fatalf("catalogs collapsed: %#v", stored)
	}
}
