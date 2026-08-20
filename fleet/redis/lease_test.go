package redis

import (
	"context"
	"testing"
	"time"
)

func TestLeaseManagerRejectsInvalidIdentityBeforeRedis(t *testing.T) {
	manager := NewLeaseManager(nil, "fleet:test")
	if _, err := manager.Acquire(context.Background(), "", "coordinator-a", time.Second); err == nil {
		t.Fatal("empty resource id was accepted")
	}
	if _, err := manager.Acquire(context.Background(), "leader/world", "", time.Second); err == nil {
		t.Fatal("empty owner was accepted")
	}
	if _, err := manager.Acquire(context.Background(), "leader/world", "coordinator-a", 0); err == nil {
		t.Fatal("zero ttl was accepted")
	}
}
