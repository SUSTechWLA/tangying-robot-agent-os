package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
)

// Use the production lease.Manager implementation with a controlled clock.
// Acquire intentionally renews a same-owner grant, just as the Redis Lua path
// does. The startup boundary must supply different owners for different starts.
func TestCoordinatorIncarnationsCannotShareOrResumeLeadership(t *testing.T) {
	for _, configured := range []string{"", "shared-deployment-label"} {
		t.Run(configured, func(t *testing.T) {
			ctx := context.Background()
			now := time.Date(2026, 10, 10, 0, 0, 0, 0, time.UTC)
			manager := lease.NewMemoryManagerWithClock(func() time.Time { return now })
			const ttl = 15 * time.Second
			const world = "incarnation-test"
			const resource = "leader/" + world
			firstOwner := coordinatorIncarnation(configured)
			secondOwner := coordinatorIncarnation(configured)
			restartedOwner := coordinatorIncarnation(configured)
			if firstOwner == secondOwner || firstOwner == restartedOwner || secondOwner == restartedOwner {
				t.Fatal("independent starts reused leadership identity")
			}
			prefix := configured
			if prefix == "" {
				prefix = "control-plane-1"
			}
			if !strings.HasPrefix(firstOwner, prefix+"/incarnation/") {
				t.Fatalf("configured name is not retained as a label: %q", firstOwner)
			}
			first, second, restarted := coordinator.New(nil), coordinator.New(nil), coordinator.New(nil)
			if err := first.WithLeadership(ctx, manager, world, firstOwner, ttl); err != nil {
				t.Fatal(err)
			}
			original, err := manager.Acquire(ctx, resource, firstOwner, ttl)
			if err != nil {
				t.Fatal(err)
			}
			if err := second.WithLeadership(ctx, manager, world, secondOwner, ttl); !errors.Is(err, lease.ErrLeaseHeld) {
				t.Fatalf("second process shared the current grant: %v", err)
			}
			if err := restarted.WithLeadership(ctx, manager, world, restartedOwner, ttl); !errors.Is(err, lease.ErrLeaseHeld) {
				t.Fatalf("restart resumed the previous incarnation's grant: %v", err)
			}
			if err := first.RenewLeadership(ctx, ttl); err != nil {
				t.Fatalf("current process cannot renew its own grant: %v", err)
			}
			now = now.Add(ttl + time.Millisecond)
			if err := restarted.WithLeadership(ctx, manager, world, restartedOwner, ttl); err != nil {
				t.Fatalf("restart cannot acquire after old lease expires: %v", err)
			}
			current, err := manager.Acquire(ctx, resource, restartedOwner, ttl)
			if err != nil || current.Token <= original.Token {
				t.Fatalf("restart did not receive a fresh fence: %+v err=%v", current, err)
			}
			if err := first.RenewLeadership(ctx, ttl); !errors.Is(err, coordinator.ErrLeadershipLost) {
				t.Fatalf("old incarnation can renew after replacement: %v", err)
			}
			if err := manager.Release(ctx, resource, firstOwner, original.Token); !errors.Is(err, lease.ErrStaleFencingToken) {
				t.Fatalf("old grant could release replacement leadership: %v", err)
			}
			if err := manager.Validate(ctx, resource, restartedOwner, current.Token); err != nil {
				t.Fatalf("old release changed current grant: %v", err)
			}
		})
	}
}
