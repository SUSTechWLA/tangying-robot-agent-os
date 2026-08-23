package cloudclient

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"log"
	"os"
	"sync"
	"time"

	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

// LinkConfig configures the mTLS gRPC Link channel to the fleet gateway.
type LinkConfig struct {
	// Address is host:port of the fleet gateway (public network).
	Address string
	// CAFile/CertFile/KeyFile are the fleet CA and this robot's client
	// certificate (mTLS).
	CAFile   string
	CertFile string
	KeyFile  string
	// ServerName is the gateway TLS server name (defaults to the host).
	ServerName                 string
	RobotID                    string
	Adapter                    string
	AdapterVersion             string
	Capabilities               []*fleetv1.Capability
	ToolCatalogRevision        string
	ToolCatalog                []*fleetv1.ToolDescriptor
	ObservationCatalogRevision string
	ObservationSources         []*fleetv1.ObservationSource
	// HeartbeatInterval and Lease are negotiated with the gateway on
	// Register; the client falls back to these when unset.
	HeartbeatInterval time.Duration
	Lease             time.Duration
}

// CommandHandler receives server commands pushed over the Link stream.
type CommandHandler func(ctx context.Context, command *fleetv1.ServerCommand)

// Link is a reconnecting mTLS gRPC channel to the fleet gateway. It keeps a
// single Link stream alive, renewing the device lease with heartbeats and
// relaying server commands to the handler. SendTelemetry is safe to call
// from any goroutine; it returns an error while the channel is down so the
// caller can fall back to the HTTP data plane.
type Link struct {
	config   LinkConfig
	handler  CommandHandler
	stop     chan struct{}
	stopOnce sync.Once
	done     chan struct{}

	mu        sync.Mutex
	stream    fleetv1.FleetGateway_LinkClient
	sendMu    sync.Mutex
	connected bool
	sequence  uint64
	leaseMS   uint32
	interval  time.Duration
}

// SetCommandHandler installs (or replaces) the server-command handler. It
// must be called before Run.
func (l *Link) SetCommandHandler(handler CommandHandler) {
	l.handler = handler
}

func NewLink(config LinkConfig, handler CommandHandler) (*Link, error) {
	if config.Address == "" {
		return nil, errors.New("fleet gRPC address is required")
	}
	if config.CAFile == "" || config.CertFile == "" || config.KeyFile == "" {
		return nil, errors.New("fleet mTLS CA, certificate and key are required")
	}
	if config.RobotID == "" {
		return nil, errors.New("robot id is required")
	}
	if config.HeartbeatInterval <= 0 {
		config.HeartbeatInterval = 5 * time.Second
	}
	if config.Lease <= 0 {
		config.Lease = 15 * time.Second
	}
	if config.ServerName == "" {
		config.ServerName = "localhost"
	}
	return &Link{config: config, handler: handler, stop: make(chan struct{}), done: make(chan struct{}), interval: config.HeartbeatInterval}, nil
}

// Run blocks until Close, reconnecting with exponential backoff after every
// disconnect (断线重连).
func (l *Link) Run(ctx context.Context) {
	defer close(l.done)
	backoff := time.Second
	for {
		select {
		case <-l.stop:
			return
		case <-ctx.Done():
			return
		default:
		}
		err := l.connectAndLink(ctx)
		if err == nil {
			return
		}
		log.Printf("edge-worker: fleet link disconnected: %v (reconnecting in %s)", err, backoff)
		select {
		case <-l.stop:
			return
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff *= 2
		if backoff > 30*time.Second {
			backoff = 30 * time.Second
		}
	}
}

func (l *Link) connectAndLink(ctx context.Context) error {
	transport, err := l.credentials()
	if err != nil {
		return err
	}
	connection, err := grpc.NewClient(l.config.Address, grpc.WithTransportCredentials(transport))
	if err != nil {
		return err
	}
	defer connection.Close()
	client := fleetv1.NewFleetGatewayClient(connection)

	registerCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	registered, err := client.Register(registerCtx, &fleetv1.RegisterRequest{
		RobotId:                    l.config.RobotID,
		Adapter:                    l.config.Adapter,
		ProtocolVersion:            "fleet.v1",
		Capabilities:               l.config.Capabilities,
		ToolCatalogRevision:        l.config.ToolCatalogRevision,
		ToolCatalog:                l.config.ToolCatalog,
		ObservationCatalogRevision: l.config.ObservationCatalogRevision,
		ObservationSources:         l.config.ObservationSources,
		AdapterVersion:             l.config.AdapterVersion,
	})
	if err != nil {
		return fmt.Errorf("register: %w", err)
	}
	if !registered.Accepted {
		return fmt.Errorf("register rejected: %s", registered.Reason)
	}
	if registered.HeartbeatIntervalMs > 0 {
		l.interval = time.Duration(registered.HeartbeatIntervalMs) * time.Millisecond
	}
	if registered.LeaseMs > 0 {
		l.leaseMS = registered.LeaseMs
	}

	linkCtx, linkCancel := context.WithCancel(ctx)
	defer linkCancel()
	stream, err := client.Link(linkCtx)
	if err != nil {
		return fmt.Errorf("link: %w", err)
	}
	l.mu.Lock()
	l.stream = stream
	l.connected = true
	l.mu.Unlock()
	defer func() {
		l.mu.Lock()
		l.stream = nil
		l.connected = false
		l.mu.Unlock()
	}()
	log.Printf("edge-worker: fleet link established for %s (heartbeat %s, lease %s)", l.config.RobotID, l.interval, time.Duration(l.leaseMS)*time.Millisecond)

	// Initial heartbeat registers presence on the stream.
	if err := l.sendHeartbeat(ctx); err != nil {
		return err
	}

	recvDone := make(chan error, 1)
	go func() { recvDone <- l.recvLoop(stream) }()

	ticker := time.NewTicker(l.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-l.stop:
			return nil
		case err := <-recvDone:
			return err
		case <-ticker.C:
			if err := l.sendHeartbeat(ctx); err != nil {
				return err
			}
		}
	}
}

func (l *Link) recvLoop(stream fleetv1.FleetGateway_LinkClient) error {
	for {
		message, err := stream.Recv()
		if err != nil {
			return err
		}
		switch payload := message.Payload.(type) {
		case *fleetv1.LinkMessage_Command:
			if l.handler != nil {
				go l.handler(context.Background(), payload.Command)
			}
		case *fleetv1.LinkMessage_Ack:
			// Heartbeat/telemetry acknowledgements are logged at debug level.
		}
	}
}

func (l *Link) sendHeartbeat(ctx context.Context) error {
	sequence := l.nextSequence()
	message := &fleetv1.LinkMessage{
		Sequence: sequence,
		Payload: &fleetv1.LinkMessage_Heartbeat{Heartbeat: &fleetv1.Heartbeat{
			RobotId: l.config.RobotID, SentUnixMs: time.Now().UnixMilli(),
		}},
	}
	return l.send(ctx, message)
}

// SendTelemetry streams one telemetry sample over the Link channel. It
// returns an error when the channel is not connected so callers can fall
// back to the HTTP data plane.
func (l *Link) SendTelemetry(ctx context.Context, sample *fleetv1.TelemetrySample) error {
	sequence := l.nextSequence()
	message := &fleetv1.LinkMessage{Sequence: sequence, Payload: &fleetv1.LinkMessage_Telemetry{Telemetry: sample}}
	return l.send(ctx, message)
}

// SendObservation publishes one typed environment fact over the robot's
// authenticated mTLS session.
func (l *Link) SendObservation(ctx context.Context, observation *fleetv1.ObservationEnvelope) error {
	sequence := l.nextSequence()
	message := &fleetv1.LinkMessage{Sequence: sequence, Payload: &fleetv1.LinkMessage_Observation{Observation: observation}}
	return l.send(ctx, message)
}

// Connected reports whether the Link channel is currently established.
func (l *Link) Connected() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.connected
}

func (l *Link) nextSequence() uint64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.sequence++
	return l.sequence
}

func (l *Link) send(ctx context.Context, message *fleetv1.LinkMessage) error {
	l.mu.Lock()
	stream := l.stream
	connected := l.connected
	l.mu.Unlock()
	if !connected || stream == nil {
		return errors.New("fleet link is not connected")
	}
	l.sendMu.Lock()
	defer l.sendMu.Unlock()
	return stream.Send(message)
}

// Close stops the link loop.
func (l *Link) Close() {
	l.stopOnce.Do(func() { close(l.stop) })
	<-l.done
}

func (l *Link) credentials() (credentials.TransportCredentials, error) {
	caBytes, err := os.ReadFile(l.config.CAFile)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caBytes) {
		return nil, errors.New("invalid fleet CA certificate")
	}
	certificate, err := tls.LoadX509KeyPair(l.config.CertFile, l.config.KeyFile)
	if err != nil {
		return nil, err
	}
	return credentials.NewTLS(&tls.Config{
		MinVersion:   tls.VersionTLS13,
		RootCAs:      roots,
		Certificates: []tls.Certificate{certificate},
		ServerName:   l.config.ServerName,
	}), nil
}
