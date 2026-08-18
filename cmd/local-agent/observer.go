package main

import (
	"context"
	"log"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

type telemetrySource interface {
	Telemetry(context.Context, string) (telemetry.Snapshot, error)
}

type telemetrySink func(context.Context, telemetry.Snapshot) error

const telemetryObservationTimeout = 5 * time.Second

// startTelemetryObserver publishes immediately, then periodically. Runtime and
// sink failures are observability failures: they are retried without taking the
// task executor or Console down.
func startTelemetryObserver(
	ctx context.Context,
	source telemetrySource,
	interval time.Duration,
	publish telemetrySink,
) <-chan struct{} {
	done := make(chan struct{})
	if interval <= 0 {
		interval = time.Second
	}
	go func() {
		defer close(done)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		lastLogged := time.Time{}
		observe := func() {
			sampleContext, cancel := context.WithTimeout(ctx, telemetryObservationTimeout)
			defer cancel()
			snapshot, err := source.Telemetry(sampleContext, "")
			if err == nil {
				err = publish(sampleContext, snapshot)
			}
			if err != nil && (lastLogged.IsZero() || time.Since(lastLogged) >= 30*time.Second) {
				log.Printf("telemetry observer: %v", err)
				lastLogged = time.Now()
			}
		}
		observe()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				observe()
			}
		}
	}()
	return done
}
