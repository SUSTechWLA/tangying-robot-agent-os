package worker

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestRetryTaskRecoversAfterTransientControlPlaneCatalogLoss(t *testing.T) {
	attempts := 0
	err := retryTask(context.Background(), 4, time.Millisecond, func() error {
		attempts++
		if attempts < 3 {
			return errors.New("robot tool catalog revision is missing")
		}
		return nil
	})

	if err != nil {
		t.Fatalf("retry task: %v", err)
	}
	if attempts != 3 {
		t.Fatalf("attempts=%d want=3", attempts)
	}
}

func TestRetryTaskStopsPromptlyOnCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	attempts := 0
	err := retryTask(ctx, 10, time.Hour, func() error {
		attempts++
		cancel()
		return errors.New("control plane unavailable")
	})

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error=%v want context canceled", err)
	}
	if attempts != 1 {
		t.Fatalf("attempts=%d want=1", attempts)
	}
}
