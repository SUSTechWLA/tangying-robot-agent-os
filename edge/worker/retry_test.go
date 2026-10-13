package worker

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type cancelRetryCloud struct {
	activityCloud
	cancel context.CancelFunc
}

func (c *cancelRetryCloud) GetTask(context.Context, string) (*tasks.Task, error) {
	c.cancel()
	return nil, errors.New("control plane unavailable")
}

func TestDurableRetryStopsPromptlyOnCancellationAndRetainsNotification(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	store := openInboxStore(t, filepath.Join(t.TempDir(), "execution.db"))
	source := &receiptSource{next: func(context.Context) (string, error) { return "pending", nil }}
	worker := New(Config{RobotID: "robot-7", ExecutionStore: store, Source: source,
		Cloud: &cancelRetryCloud{cancel: cancel}, Runtime: activityRuntime{}, TaskRetryDelay: time.Hour,
		TelemetryInterval: time.Hour})
	started := time.Now()
	if err := worker.Run(ctx); !errors.Is(err, context.Canceled) || time.Since(started) > time.Second {
		t.Fatalf("error=%v duration=%v", err, time.Since(started))
	}
	_, saved, err := worker.loadNotification(context.Background())
	if err != nil || saved.TaskID != "pending" {
		t.Fatalf("saved=%+v err=%v", saved, err)
	}
}
