package lease

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestTransferInvalidatesPreviousFencingToken(t *testing.T) {
	manager := NewMemoryManager()
	first, err := manager.Acquire(context.Background(), "object/red-block", "robot-1", time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	second, err := manager.Transfer(context.Background(), "object/red-block", "robot-1", "robot-2", first.Token, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if second.Token <= first.Token {
		t.Fatalf("tokens %d -> %d", first.Token, second.Token)
	}
	if err := manager.Validate(context.Background(), "object/red-block", "robot-1", first.Token); !errors.Is(err, ErrStaleFencingToken) {
		t.Fatalf("old fencing error=%v", err)
	}
}

func TestExpiredGrantCanBeAcquiredButTokenKeepsIncreasing(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	manager := NewMemoryManagerWithClock(func() time.Time { return now })
	first, err := manager.Acquire(context.Background(), "leader/world-test", "coordinator-a", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(2 * time.Second)
	second, err := manager.Acquire(context.Background(), "leader/world-test", "coordinator-b", time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if second.Token <= first.Token || second.Owner != "coordinator-b" {
		t.Fatalf("grants %#v -> %#v", first, second)
	}
}
