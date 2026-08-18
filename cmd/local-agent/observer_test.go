package main

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

type fakeTelemetrySource struct {
	mu       sync.Mutex
	snapshot telemetry.Snapshot
	errors   []error
	calls    int
}

func (s *fakeTelemetrySource) Telemetry(context.Context, string) (telemetry.Snapshot, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls++
	if len(s.errors) > 0 {
		err := s.errors[0]
		s.errors = s.errors[1:]
		return telemetry.Snapshot{}, err
	}
	return s.snapshot, nil
}

func (s *fakeTelemetrySource) callCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.calls
}

func TestObserverPublishesImmediatelyAndStopsWithContext(t *testing.T) {
	source := &fakeTelemetrySource{snapshot: telemetry.Snapshot{Adapter: "mujoco"}}
	sink := make(chan telemetry.Snapshot, 1)
	ctx, cancel := context.WithCancel(context.Background())
	done := startTelemetryObserver(ctx, source, 10*time.Millisecond, func(_ context.Context, got telemetry.Snapshot) error {
		sink <- got
		return nil
	})

	select {
	case got := <-sink:
		if got.Adapter != "mujoco" {
			t.Fatalf("adapter = %q", got.Adapter)
		}
	case <-time.After(time.Second):
		t.Fatal("no startup telemetry")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("observer did not stop")
	}
}

func TestObserverRetriesSourceAndSinkErrors(t *testing.T) {
	source := &fakeTelemetrySource{
		snapshot: telemetry.Snapshot{Adapter: "mujoco"},
		errors:   []error{errors.New("runtime unavailable")},
	}
	published := make(chan struct{}, 1)
	sinkCalls := 0
	ctx, cancel := context.WithCancel(context.Background())
	done := startTelemetryObserver(ctx, source, 5*time.Millisecond, func(_ context.Context, _ telemetry.Snapshot) error {
		sinkCalls++
		if sinkCalls == 1 {
			return errors.New("temporary sink error")
		}
		published <- struct{}{}
		return nil
	})

	select {
	case <-published:
	case <-time.After(time.Second):
		t.Fatalf("observer did not recover, source calls = %d sink calls = %d", source.callCount(), sinkCalls)
	}
	cancel()
	<-done
	if source.callCount() < 3 {
		t.Fatalf("source calls = %d, want at least 3", source.callCount())
	}
}
