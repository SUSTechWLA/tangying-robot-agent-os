package cloudclient

import (
	"context"
	"errors"
	"testing"
	"time"

	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
	"google.golang.org/grpc"
)

type blockedLinkStream struct {
	grpc.ClientStream
	ctx     context.Context
	started chan struct{}
}

func (s *blockedLinkStream) Send(*fleetv1.LinkMessage) error {
	close(s.started)
	<-s.ctx.Done()
	return s.ctx.Err()
}
func (s *blockedLinkStream) Recv() (*fleetv1.LinkMessage, error) {
	<-s.ctx.Done()
	return nil, s.ctx.Err()
}

func TestLinkSendCancellationInterruptsBlockedStream(t *testing.T) {
	streamCtx, closeStream := context.WithCancel(context.Background())
	defer closeStream()
	stream := &blockedLinkStream{ctx: streamCtx, started: make(chan struct{})}
	link := &Link{stream: stream, streamCancel: closeStream, connected: true}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- link.SendTelemetry(ctx, &fleetv1.TelemetrySample{}) }()
	<-stream.started
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("telemetry shutdown remained stuck inside Send")
	}
}

func TestLinkDoesNotSendAfterProducerContextExpired(t *testing.T) {
	stream := &blockedLinkStream{ctx: context.Background(), started: make(chan struct{})}
	link := &Link{stream: stream, connected: true}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := link.SendTelemetry(ctx, &fleetv1.TelemetrySample{}); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	select {
	case <-stream.started:
		t.Fatal("cancelled producer dispatched")
	default:
	}
}
