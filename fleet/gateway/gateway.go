// Package gateway is the cloud endpoint that robot-side edge workers dial
// over the public network: mTLS-only gRPC with a bidirectional Link stream.
//
// The Link stream carries:
//
//   - Heartbeat up: renews the device lease; the gateway replies with an Ack
//     carrying the granted lease expiry.
//   - Telemetry / status / event reports up: ingested into the fleet
//     telemetry sink and task event log.
//   - Server commands down: cancel / emergency stop / ping pushed by the
//     control plane to a connected robot.
//
// Task pulling and step state transitions stay on HTTP (or Redis Streams),
// so the data plane works independently of the control channel and the
// gateway stays small.
package gateway

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net"
	"os"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
)

// Options configures the gateway. CAFile/CertFile/KeyFile are mandatory:
// the gateway never serves plaintext gRPC.
type Options struct {
	CAFile            string
	CertFile          string
	KeyFile           string
	Registry          *registry.Registry
	Telemetry         telemetry.Store
	Tasks             *tasks.Service
	Lease             time.Duration
	HeartbeatInterval time.Duration
	RequireCNMatch    bool
	World             *worldhub.Hub
}

// Server is the mTLS FleetGateway gRPC server.
type Server struct {
	fleetv1.UnimplementedFleetGatewayServer
	options    Options
	mu         sync.Mutex
	sessions   map[string]*session
	grpcServer *grpc.Server
	listener   net.Listener
}

type session struct {
	robotID string
	sendCh  chan *fleetv1.LinkMessage
	done    chan struct{}
	once    sync.Once
}

func (s *session) close() {
	s.once.Do(func() { close(s.done) })
}

func (s *session) send(message *fleetv1.LinkMessage) bool {
	select {
	case s.sendCh <- message:
		return true
	case <-s.done:
		return false
	}
}

func New(options Options) (*Server, error) {
	if options.CAFile == "" || options.CertFile == "" || options.KeyFile == "" {
		return nil, errors.New("gateway requires CA, certificate and key files (mTLS only)")
	}
	if options.Registry == nil {
		return nil, errors.New("gateway requires a device registry")
	}
	if options.Telemetry == nil {
		return nil, errors.New("gateway requires a telemetry store")
	}
	if options.Lease <= 0 {
		options.Lease = 15 * time.Second
	}
	if options.HeartbeatInterval <= 0 {
		options.HeartbeatInterval = 5 * time.Second
	}
	return &Server{options: options, sessions: map[string]*session{}}, nil
}

// Serve listens on the given address and serves gRPC until Close.
func (s *Server) Serve(address string) error {
	credentials, err := s.tlsCredentials()
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}
	s.listener = listener
	s.grpcServer = grpc.NewServer(grpc.Creds(credentials))
	fleetv1.RegisterFleetGatewayServer(s.grpcServer, s)
	log.Printf("fleet gateway listening on %s (mTLS, lease=%s)", address, s.options.Lease)
	return s.grpcServer.Serve(listener)
}

// Close stops the gRPC server and closes every session.
func (s *Server) Close() {
	s.mu.Lock()
	for robotID, session := range s.sessions {
		session.close()
		delete(s.sessions, robotID)
	}
	s.mu.Unlock()
	if s.grpcServer != nil {
		s.grpcServer.Stop()
	}
}

func (s *Server) tlsCredentials() (credentials.TransportCredentials, error) {
	caBytes, err := os.ReadFile(s.options.CAFile)
	if err != nil {
		return nil, err
	}
	clientCAs := x509.NewCertPool()
	if !clientCAs.AppendCertsFromPEM(caBytes) {
		return nil, errors.New("invalid client CA certificate")
	}
	certificate, err := tls.LoadX509KeyPair(s.options.CertFile, s.options.KeyFile)
	if err != nil {
		return nil, err
	}
	return credentials.NewTLS(&tls.Config{
		MinVersion:   tls.VersionTLS13,
		Certificates: []tls.Certificate{certificate},
		ClientCAs:    clientCAs,
		ClientAuth:   tls.RequireAndVerifyClientCert,
	}), nil
}

// peerCN extracts the mTLS client certificate common name, if any.
func peerCN(ctx context.Context) string {
	info, ok := peer.FromContext(ctx)
	if !ok || info.AuthInfo == nil {
		return ""
	}
	tlsInfo, ok := info.AuthInfo.(credentials.TLSInfo)
	if !ok || len(tlsInfo.State.PeerCertificates) == 0 {
		return ""
	}
	return tlsInfo.State.PeerCertificates[0].Subject.CommonName
}

// Register claims a robot identity. Robot identity is always derived from the
// verified mTLS client certificate; configuration must never weaken this
// binding on a robot-scoped endpoint.
func (s *Server) Register(ctx context.Context, request *fleetv1.RegisterRequest) (*fleetv1.RegisterResponse, error) {
	if request.RobotId == "" {
		return nil, status.Error(codes.InvalidArgument, "robot_id is required")
	}
	cn := peerCN(ctx)
	if cn == "" || cn != request.RobotId {
		return nil, status.Errorf(codes.PermissionDenied, "certificate CN %q does not match robot id %q", cn, request.RobotId)
	}
	existing, registered, err := s.options.Registry.Status(ctx, request.RobotId)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "registration lookup failed: %v", err)
	}
	if registered && existing.Online && existing.Adapter != "" && request.Adapter != "" && existing.Adapter != request.Adapter {
		return nil, status.Errorf(
			codes.FailedPrecondition,
			"robot identity %s is already leased by adapter %s",
			request.RobotId,
			existing.Adapter,
		)
	}
	capabilities := make([]registry.Capability, 0, len(request.Capabilities))
	for _, capability := range request.Capabilities {
		capabilities = append(capabilities, registry.Capability{Name: capability.Name, Available: capability.Available})
	}
	toolCatalog := make([]registry.ToolDescriptor, 0, len(request.ToolCatalog))
	for _, tool := range request.ToolCatalog {
		toolCatalog = append(toolCatalog, registry.ToolDescriptor{
			Name: tool.Name, Description: tool.Description, DisplayName: tool.DisplayName, Purpose: tool.Purpose,
			InputParameters: append([]string(nil), tool.InputParameters...), OutputParameters: append([]string(nil), tool.OutputParameters...),
			SafeArgumentNames: append([]string(nil), tool.SafeArgumentNames...),
			SideEffectClass:   tool.SideEffectClass, SafetyLevel: tool.SafetyLevel, Available: tool.Available,
		})
	}
	observationSources := make([]registry.ObservationSource, 0, len(request.ObservationSources))
	for _, source := range request.ObservationSources {
		observationSources = append(observationSources, registry.ObservationSource{
			SourceID: source.SourceId, SourceType: source.SourceType, SchemaRevision: source.SchemaRevision,
			FrameIDs: append([]string(nil), source.FrameIds...), TransformRevision: source.TransformRevision,
			UpdateRateHz: source.UpdateRateHz, FreshnessBudgetMS: source.FreshnessBudgetMs,
			PayloadKinds: append([]string(nil), source.PayloadKinds...), AdapterVersion: source.AdapterVersion,
		})
	}
	expiry, err := s.options.Registry.Register(ctx, registry.Device{
		RobotID:                    request.RobotId,
		Adapter:                    request.Adapter,
		SoftwareVersion:            request.SoftwareVersion,
		ProtocolVersion:            request.ProtocolVersion,
		RuntimeVersion:             request.RuntimeVersion,
		Capabilities:               capabilities,
		ToolCatalogRevision:        request.ToolCatalogRevision,
		ToolCatalog:                toolCatalog,
		ObservationCatalogRevision: request.ObservationCatalogRevision,
		ObservationSources:         observationSources,
		AdapterVersion:             request.AdapterVersion,
		Address:                    peerAddress(ctx),
	}, s.options.Lease)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "register failed: %v", err)
	}
	log.Printf("fleet gateway: robot %s registered (lease until %s)", request.RobotId, expiry)
	return &fleetv1.RegisterResponse{
		Accepted:            true,
		RobotId:             request.RobotId,
		HeartbeatIntervalMs: uint32(s.options.HeartbeatInterval.Milliseconds()),
		LeaseMs:             uint32(s.options.Lease.Milliseconds()),
	}, nil
}

// Link is the bidirectional presence and telemetry stream.
func (s *Server) Link(stream fleetv1.FleetGateway_LinkServer) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	if first.GetHeartbeat() == nil {
		return status.Error(codes.InvalidArgument, "first Link message must be a heartbeat")
	}
	robotID := first.GetHeartbeat().RobotId
	if robotID == "" {
		return status.Error(codes.InvalidArgument, "heartbeat robot_id is required")
	}
	certificateRobotID := peerCN(stream.Context())
	if certificateRobotID == "" || certificateRobotID != robotID {
		return status.Errorf(codes.PermissionDenied, "certificate CN %q does not match Link robot id %q", certificateRobotID, robotID)
	}
	device, registered, registryErr := s.options.Registry.Status(stream.Context(), robotID)
	if registryErr != nil {
		return status.Errorf(codes.Internal, "registration lookup failed: %v", registryErr)
	}
	if !registered || !device.Online {
		return status.Error(codes.FailedPrecondition, "robot must register with the same certificate before opening Link")
	}
	session := &session{robotID: robotID, sendCh: make(chan *fleetv1.LinkMessage, 16), done: make(chan struct{})}
	s.mu.Lock()
	if existing, ok := s.sessions[robotID]; ok {
		existing.close()
	}
	s.sessions[robotID] = session
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		if current, ok := s.sessions[robotID]; ok && current == session {
			delete(s.sessions, robotID)
		}
		s.mu.Unlock()
		session.close()
	}()

	go s.sendLoop(stream, session)
	if err := s.handleHeartbeat(stream, session, first); err != nil {
		return err
	}
	for {
		message, err := stream.Recv()
		if err != nil {
			return err
		}
		switch payload := message.Payload.(type) {
		case *fleetv1.LinkMessage_Heartbeat:
			if err := s.handleHeartbeat(stream, session, message); err != nil {
				return err
			}
		case *fleetv1.LinkMessage_Telemetry:
			if err := s.handleTelemetry(stream, session, message, payload.Telemetry); err != nil {
				return err
			}
		case *fleetv1.LinkMessage_Observation:
			if err := s.handleObservation(stream, session, message, payload.Observation); err != nil {
				return err
			}
		case *fleetv1.LinkMessage_Event:
			s.handleEventReport(message, payload.Event)
		case *fleetv1.LinkMessage_Status:
			s.handleStatusReport(payload.Status)
		case *fleetv1.LinkMessage_Command:
			session.send(ack(message, false, "server commands are not accepted from devices"))
		case *fleetv1.LinkMessage_Ack:
			// Client acknowledges a pushed command; nothing to do.
		default:
			return status.Error(codes.InvalidArgument, "unknown Link message payload")
		}
	}
}

func (s *Server) handleObservation(stream fleetv1.FleetGateway_LinkServer, session *session, message *fleetv1.LinkMessage, wire *fleetv1.ObservationEnvelope) error {
	if s.options.World == nil {
		return status.Error(codes.FailedPrecondition, "world observation sink is not configured")
	}
	if wire == nil || wire.RobotId != session.robotID {
		return status.Error(codes.InvalidArgument, "observation robot_id mismatch")
	}
	envelope, err := observationFromProto(wire)
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "invalid observation: %v", err)
	}
	if _, err := s.options.World.Ingest(stream.Context(), envelope); err != nil {
		return status.Errorf(codes.InvalidArgument, "observation rejected: %v", err)
	}
	session.send(ack(message, true, "observation accepted"))
	return nil
}

// sendLoop is the only goroutine that writes to the gRPC stream.
func (s *Server) sendLoop(stream fleetv1.FleetGateway_LinkServer, session *session) {
	for {
		select {
		case message := <-session.sendCh:
			if err := stream.Send(message); err != nil {
				session.close()
				return
			}
		case <-session.done:
			return
		}
	}
}

func (s *Server) handleHeartbeat(stream fleetv1.FleetGateway_LinkServer, session *session, message *fleetv1.LinkMessage) error {
	heartbeat := message.GetHeartbeat()
	if heartbeat == nil || heartbeat.RobotId != session.robotID {
		return status.Error(codes.InvalidArgument, "heartbeat robot_id mismatch")
	}
	expiry, err := s.options.Registry.Heartbeat(stream.Context(), session.robotID, s.options.Lease)
	if err != nil {
		return status.Errorf(codes.Internal, "heartbeat failed: %v", err)
	}
	response := &fleetv1.LinkMessage{
		Sequence: message.Sequence,
		Payload: &fleetv1.LinkMessage_Ack{Ack: &fleetv1.Ack{
			Sequence: message.Sequence, Ok: true, Message: "lease granted", LeaseExpiryUnixMs: expiry.UnixMilli(),
		}},
	}
	session.send(response)
	return nil
}

func (s *Server) handleTelemetry(stream fleetv1.FleetGateway_LinkServer, session *session, message *fleetv1.LinkMessage, sample *fleetv1.TelemetrySample) error {
	if sample.RobotId != session.robotID {
		return status.Error(codes.InvalidArgument, "telemetry robot_id mismatch")
	}
	if err := s.options.Telemetry.Ingest(stream.Context(), sampleFromProto(sample)); err != nil {
		return status.Errorf(codes.Internal, "telemetry ingest failed: %v", err)
	}
	session.send(ack(message, true, "telemetry accepted"))
	return nil
}

func (s *Server) handleEventReport(message *fleetv1.LinkMessage, report *fleetv1.EventReport) {
	if s.options.Tasks == nil || report.TaskId == "" {
		return
	}
	_, _ = s.options.Tasks.AppendEvent(context.Background(), report.TaskId, tasks.TaskEvent{
		Type:    report.EventType,
		StepID:  report.StepId,
		Message: report.Message,
		Payload: stringMapToAny(report.Payload),
	})
	session, ok := s.session(report.RobotId)
	if ok {
		session.send(ack(message, true, "event accepted"))
	}
}

func (s *Server) handleStatusReport(report *fleetv1.StatusReport) {
	// Status is derivable from heartbeat/lease and task events; a dedicated
	// status history table is future work.
	log.Printf("fleet gateway: robot %s status %s: %s", report.RobotId, report.Status, report.Message)
}

func (s *Server) session(robotID string) (*session, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	session, ok := s.sessions[robotID]
	return session, ok
}

// PushCommand sends a server command (cancel_step / emergency_stop / ping)
// to a connected robot's Link stream.
func (s *Server) PushCommand(robotID, commandType string, args map[string]string) error {
	session, ok := s.session(robotID)
	if !ok {
		return fmt.Errorf("robot %s has no active Link session", robotID)
	}
	command := &fleetv1.ServerCommand{CommandId: fmt.Sprintf("cmd-%d", time.Now().UnixNano()), Type: commandType, Args: args}
	if !session.send(&fleetv1.LinkMessage{Payload: &fleetv1.LinkMessage_Command{Command: command}}) {
		return fmt.Errorf("robot %s session is closing", robotID)
	}
	return nil
}

func peerAddress(ctx context.Context) string {
	info, ok := peer.FromContext(ctx)
	if !ok || info.Addr == nil {
		return ""
	}
	return info.Addr.String()
}

func ack(message *fleetv1.LinkMessage, ok bool, note string) *fleetv1.LinkMessage {
	return &fleetv1.LinkMessage{
		Sequence: message.Sequence,
		Payload: &fleetv1.LinkMessage_Ack{Ack: &fleetv1.Ack{
			Sequence: message.Sequence, Ok: ok, Message: note,
		}},
	}
}

func sampleFromProto(sample *fleetv1.TelemetrySample) telemetry.Sample {
	converted := telemetry.Sample{
		RobotID:          sample.RobotId,
		ObservedAt:       time.UnixMilli(sample.ObservedUnixMs),
		Pose:             append([]float64(nil), sample.PoseXyzYaw...),
		Activity:         sample.Activity,
		EmergencyStopped: sample.EmergencyStopped,
		Anomalies:        append([]string(nil), sample.Anomalies...),
		State:            map[string]float64{},
	}
	for key, value := range sample.State {
		converted.State[key] = value
	}
	for _, entity := range sample.Entities {
		converted.Entities = append(converted.Entities, telemetry.Entity{
			EntityID:   entity.EntityId,
			Category:   entity.Category,
			Attributes: entity.Attributes,
			Pose:       append([]float64(nil), entity.PoseXyzQuat...),
			Confidence: entity.Confidence,
			Relation:   entity.Relation,
		})
	}
	if grid := sample.Occupancy; grid != nil {
		converted.Occupancy = &telemetry.OccupancyGrid{
			Width: int(grid.Width), Height: int(grid.Height), CellSize: grid.CellSizeM,
			OriginX: grid.OriginX, OriginY: grid.OriginY, Cells: append([]byte(nil), grid.Cells...),
		}
	}
	converted.Frame = append([]byte(nil), sample.Frame...)
	converted.FrameMediaType = sample.FrameMediaType
	converted.Held = sample.Held
	if len(sample.Placements) > 0 {
		converted.Placements = map[string]string{}
		for objectID, destination := range sample.Placements {
			converted.Placements[objectID] = destination
		}
	}
	if capture := sensorCaptureFromProto(sample.Capture); capture != nil {
		if err := capture.Validate(); err != nil {
			converted.Anomalies = appendUniqueAnomaly(converted.Anomalies, sensors.AnomalyInvalidCapture)
		} else {
			converted.Capture = capture
		}
	}
	return converted
}

func sensorCaptureFromProto(wire *fleetv1.SensorCapture) *sensors.Capture {
	if wire == nil {
		return nil
	}
	capture := &sensors.Capture{
		SchemaVersion: wire.SchemaVersion, CaptureID: wire.CaptureId, RobotID: wire.RobotId, EpisodeID: wire.EpisodeId,
		SimulationStep: wire.SimulationStep, SourceSequence: wire.SourceSequence,
		CapturedAt: time.UnixMilli(wire.CapturedUnixMs).UTC(), FrameID: wire.FrameId,
		TransformRevision: wire.TransformRevision, WorldRevision: wire.WorldRevision,
		Frames: make([]sensors.Frame, 0, len(wire.Frames)),
	}
	for _, frame := range wire.Frames {
		if frame == nil {
			capture.Frames = append(capture.Frames, sensors.Frame{})
			continue
		}
		capture.Frames = append(capture.Frames, sensors.Frame{
			SensorID: frame.SensorId, Modality: frame.Modality, MediaType: frame.MediaType,
			Width: int(frame.Width), Height: int(frame.Height), SHA256: frame.Sha256, URI: frame.Uri,
			DepthScaleM: frame.DepthScaleM, MinRangeM: frame.MinRangeM, MaxRangeM: frame.MaxRangeM,
			Intrinsics:    append([]float64(nil), frame.Intrinsics...),
			CameraToWorld: append([]float64(nil), frame.CameraToWorld...), Data: append([]byte(nil), frame.Data...),
		})
	}
	return capture
}

func appendUniqueAnomaly(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}

func observationFromProto(wire *fleetv1.ObservationEnvelope) (observation.Envelope, error) {
	if wire == nil {
		return observation.Envelope{}, errors.New("observation is required")
	}
	envelope := observation.Envelope{
		SchemaVersion: wire.SchemaVersion, ObservationID: wire.ObservationId,
		WorldID: wire.WorldId, SourceID: wire.SourceId, RobotID: wire.RobotId,
		SourceType: observation.SourceType(wire.SourceType), SourceSequence: wire.SourceSequence,
		ObservedAt: time.UnixMilli(wire.ObservedUnixMs).UTC(), ReceivedAt: time.UnixMilli(wire.ReceivedUnixMs).UTC(),
		FrameID: wire.FrameId, TransformRevision: wire.TransformRevision, Kind: observation.Kind(wire.Kind),
		Confidence: wire.Confidence,
	}
	if wire.Quality != nil {
		envelope.Quality = observation.Quality{LatencyMS: wire.Quality.LatencyMs, Anomalies: append([]string(nil), wire.Quality.Anomalies...)}
	}
	if wire.Causation != nil {
		envelope.Causation = observation.Causation{TaskID: wire.Causation.TaskId, CommandID: wire.Causation.CommandId}
	}
	if wire.Provenance != nil {
		envelope.Provenance = observation.Provenance{Adapter: wire.Provenance.Adapter, Version: wire.Provenance.Version, Sensor: wire.Provenance.Sensor}
	}
	if wire.FrameRef != nil {
		envelope.FrameRef = &observation.FrameRef{
			URI: wire.FrameRef.Uri, MIME: wire.FrameRef.Mime, SHA256: wire.FrameRef.Sha256,
			Width: int(wire.FrameRef.Width), Height: int(wire.FrameRef.Height),
			ObservedAt: time.UnixMilli(wire.FrameRef.ObservedUnixMs).UTC(),
		}
	}
	if wire.Payload != nil {
		values := wire.Payload.AsMap()
		var payload any
		switch envelope.Kind {
		case observation.EntityUpsert, observation.EntityDelete:
			payload = &observation.EntityPayload{}
		case observation.RobotStateUpsert:
			payload = &observation.RobotPayload{}
		case observation.ResourceUpsert:
			payload = &observation.ResourcePayload{}
		case observation.SourceHealth:
			payload = &observation.SourceHealthPayload{}
		default:
			return observation.Envelope{}, observation.ErrPayloadType
		}
		encoded, err := json.Marshal(values)
		if err != nil {
			return observation.Envelope{}, err
		}
		if err := json.Unmarshal(encoded, payload); err != nil {
			return observation.Envelope{}, err
		}
		switch typed := payload.(type) {
		case *observation.EntityPayload:
			envelope.Payload = *typed
		case *observation.RobotPayload:
			envelope.Payload = *typed
		case *observation.ResourcePayload:
			envelope.Payload = *typed
		case *observation.SourceHealthPayload:
			envelope.Payload = *typed
		}
	}
	if err := envelope.Validate(); err != nil {
		return observation.Envelope{}, err
	}
	return envelope, nil
}

func stringMapToAny(input map[string]string) map[string]any {
	if len(input) == 0 {
		return nil
	}
	output := make(map[string]any, len(input))
	for key, value := range input {
		output[key] = value
	}
	return output
}
