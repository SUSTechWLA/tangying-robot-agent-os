package gateway

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
	"google.golang.org/protobuf/types/known/structpb"
)

// certSet holds one CA and one issued certificate/key pair.
type certSet struct {
	caPEM   []byte
	certPEM []byte
	keyPEM  []byte
}

func issue(t *testing.T, ca *certSet, cn string, server bool) *certSet {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serial, _ := rand.Int(rand.Reader, big.NewInt(1<<62))
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: cn},
		NotBefore:    time.Now().Add(-time.Minute),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
	}
	if server {
		template.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
		template.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
		template.DNSNames = []string{"localhost"}
	} else {
		template.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
	}
	caKey, err := parsePrivateKey(t, ca.keyPEM)
	if err != nil {
		t.Fatal(err)
	}
	caCert, err := parseCertificate(t, ca.caPEM)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.CreateCertificate(rand.Reader, template, caCert, &key.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return &certSet{
		caPEM:   ca.caPEM,
		certPEM: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
		keyPEM:  pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}),
	}
}

func newCA(t *testing.T) *certSet {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serial, _ := rand.Int(rand.Reader, big.NewInt(1<<62))
	template := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "Test Fleet CA"},
		NotBefore:             time.Now().Add(-time.Minute),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return &certSet{
		caPEM:  pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
		keyPEM: pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}),
	}
}

func parsePrivateKey(t *testing.T, pemBytes []byte) (*ecdsa.PrivateKey, error) {
	t.Helper()
	block, _ := pem.Decode(pemBytes)
	if block == nil {
		t.Fatal("no PEM block")
	}
	return x509.ParseECPrivateKey(block.Bytes)
}

func parseCertificate(t *testing.T, pemBytes []byte) (*x509.Certificate, error) {
	t.Helper()
	block, _ := pem.Decode(pemBytes)
	if block == nil {
		t.Fatal("no PEM block")
	}
	return x509.ParseCertificate(block.Bytes)
}

func writeFiles(t *testing.T, dir string, set *certSet, suffix string) (ca, cert, key string) {
	t.Helper()
	ca = filepath.Join(dir, "ca"+suffix+".crt")
	cert = filepath.Join(dir, "cert"+suffix+".crt")
	key = filepath.Join(dir, "key"+suffix+".key")
	if err := os.WriteFile(ca, set.caPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cert, set.certPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(key, set.keyPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	return ca, cert, key
}

func startGateway(t *testing.T) (*Server, *bufconn.Listener, *certSet, context.Context) {
	t.Helper()
	ca := newCA(t)
	serverCert := issue(t, ca, "fleet-control-plane", true)
	dir := t.TempDir()
	caFile, serverCertFile, serverKeyFile := writeFiles(t, dir, serverCert, "-server")

	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	deviceRegistry := registry.New(registry.NewMemoryStore())
	telemetryStore := fleettelemetry.NewMemoryStore()
	server, err := New(Options{
		CAFile: caFile, CertFile: serverCertFile, KeyFile: serverKeyFile,
		Registry: deviceRegistry, Telemetry: telemetryStore, Tasks: service,
		Lease: 15 * time.Second, HeartbeatInterval: 5 * time.Second, RequireCNMatch: true,
		World: worldhub.New("world-test", time.Second, 32),
	})
	if err != nil {
		t.Fatal(err)
	}
	grpcServer := grpc.NewServer(grpc.Creds(server.credentialsOrFail(t)))
	fleetv1.RegisterFleetGatewayServer(grpcServer, server)
	listener := bufconn.Listen(1 << 20)
	go func() { _ = grpcServer.Serve(listener) }()
	t.Cleanup(grpcServer.Stop)
	t.Cleanup(func() { listener.Close() })
	return server, listener, ca, context.Background()
}

func TestGatewayAcceptsObservationOnlyFromBoundMTLSSession(t *testing.T) {
	server, listener, ca, ctx := startGateway(t)
	robotCert := issue(t, ca, "robot-1", false)
	connection := dial(t, listener, robotCert)
	client := fleetv1.NewFleetGatewayClient(connection)
	if _, err := client.Register(ctx, &fleetv1.RegisterRequest{RobotId: "robot-1", Adapter: "mujoco", ProtocolVersion: "fleet.v1"}); err != nil {
		t.Fatal(err)
	}
	stream, err := client.Link(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&fleetv1.LinkMessage{Sequence: 1, Payload: &fleetv1.LinkMessage_Heartbeat{Heartbeat: &fleetv1.Heartbeat{RobotId: "robot-1", SentUnixMs: time.Now().UnixMilli()}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := stream.Recv(); err != nil {
		t.Fatal(err)
	}
	payload, _ := structpb.NewStruct(map[string]any{"entityId": "red-block", "category": "block", "pose": []any{0.5, 0.3, 0.8}})
	observation := &fleetv1.ObservationEnvelope{
		SchemaVersion: "world.observation.v1", ObservationId: "obs-1", WorldId: "world-test",
		SourceId: "robot-1/scene", RobotId: "robot-1", SourceType: "sim_ground_truth", SourceSequence: 1,
		ObservedUnixMs: time.Now().UnixMilli(), ReceivedUnixMs: time.Now().UnixMilli(), FrameId: "world",
		TransformRevision: "scene-v1", Kind: "entity_upsert", Payload: payload, Confidence: 1,
		Provenance: &fleetv1.ObservationProvenance{Adapter: "mujoco", Version: "0.1.0"},
	}
	if err := stream.Send(&fleetv1.LinkMessage{Sequence: 2, Payload: &fleetv1.LinkMessage_Observation{Observation: observation}}); err != nil {
		t.Fatal(err)
	}
	ack, err := stream.Recv()
	if err != nil || ack.GetAck() == nil || !ack.GetAck().Ok {
		t.Fatalf("ack=%#v err=%v", ack, err)
	}
	snapshot, err := server.options.World.Snapshot(ctx)
	if err != nil || snapshot.Revision != 1 || snapshot.Entities["red-block"].EntityID != "red-block" {
		t.Fatalf("snapshot=%#v err=%v", snapshot, err)
	}
}

func (s *Server) credentialsOrFail(t *testing.T) credentials.TransportCredentials {
	t.Helper()
	creds, err := s.tlsCredentials()
	if err != nil {
		t.Fatal(err)
	}
	return creds
}

func dial(t *testing.T, listener *bufconn.Listener, client *certSet) *grpc.ClientConn {
	t.Helper()
	block, _ := pem.Decode(client.caPEM)
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(client.caPEM) {
		t.Fatal("bad CA PEM")
	}
	cert, err := tls.X509KeyPair(client.certPEM, client.keyPEM)
	if err != nil {
		t.Fatal(err)
	}
	transport := credentials.NewTLS(&tls.Config{
		MinVersion: tls.VersionTLS13, RootCAs: pool, Certificates: []tls.Certificate{cert}, ServerName: "localhost",
	})
	connection, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }),
		grpc.WithTransportCredentials(transport))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { connection.Close() })
	_ = block
	return connection
}

func TestGatewayRegistersAndLinksWithHeartbeat(t *testing.T) {
	server, listener, ca, ctx := startGateway(t)
	robotCert := issue(t, ca, "robot-1", false)
	connection := dial(t, listener, robotCert)
	client := fleetv1.NewFleetGatewayClient(connection)

	registered, err := client.Register(ctx, &fleetv1.RegisterRequest{RobotId: "robot-1", Adapter: "mujoco", ProtocolVersion: "fleet.v1"})
	if err != nil {
		t.Fatal(err)
	}
	if !registered.Accepted || registered.RobotId != "robot-1" {
		t.Fatalf("register = %+v", registered)
	}
	if registered.LeaseMs != 15000 || registered.HeartbeatIntervalMs != 5000 {
		t.Fatalf("negotiated policy = %+v", registered)
	}

	// Open the Link stream and send a heartbeat; the server must ack with a
	// lease expiry.
	stream, err := client.Link(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&fleetv1.LinkMessage{Sequence: 1, Payload: &fleetv1.LinkMessage_Heartbeat{Heartbeat: &fleetv1.Heartbeat{RobotId: "robot-1", SentUnixMs: time.Now().UnixMilli()}}}); err != nil {
		t.Fatal(err)
	}
	ack, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if ack.GetAck() == nil || !ack.GetAck().Ok || ack.GetAck().LeaseExpiryUnixMs == 0 {
		t.Fatalf("heartbeat ack = %+v", ack)
	}

	// Telemetry over the stream is ingested and acked.
	if err := stream.Send(&fleetv1.LinkMessage{Sequence: 2, Payload: &fleetv1.LinkMessage_Telemetry{Telemetry: &fleetv1.TelemetrySample{
		RobotId: "robot-1", ObservedUnixMs: time.Now().UnixMilli(), PoseXyzYaw: []float64{0, 0, 0, 0},
	}}}); err != nil {
		t.Fatal(err)
	}
	ack, err = stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if ack.GetAck() == nil || !ack.GetAck().Ok {
		t.Fatalf("telemetry ack = %+v", ack)
	}

	// The device lease is live after register+heartbeat.
	device, ok, err := server.options.Registry.Status(ctx, "robot-1")
	if err != nil || !ok {
		t.Fatalf("registry status err=%v ok=%v", err, ok)
	}
	if !device.Online {
		t.Fatal("device must be online after register+heartbeat")
	}
}

func TestSampleFromProtoCopiesSynchronizedRGBDCapture(t *testing.T) {
	wire := validFleetCapture()
	converted := sampleFromProto(&fleetv1.TelemetrySample{
		RobotId: "robot1", Frame: []byte("overview"), FrameMediaType: "image/png", Capture: wire,
	})
	wire.Frames[0].Data[0] = 'X'
	if converted.Capture == nil || string(converted.Capture.Frames[0].Data) != "rgb-frame" {
		t.Fatalf("wire storage leaked into Fleet sample: %#v", converted.Capture)
	}
	if string(converted.Frame) != "overview" || converted.FrameMediaType != "image/png" {
		t.Fatalf("legacy overview missing: %#v", converted)
	}
}

func TestSampleFromProtoDropsInvalidCaptureButKeepsLegacyState(t *testing.T) {
	wire := validFleetCapture()
	wire.Frames[0].Sha256 = "invalid"
	converted := sampleFromProto(&fleetv1.TelemetrySample{
		RobotId: "robot1", Frame: []byte("overview"), FrameMediaType: "image/png", Capture: wire,
	})
	if converted.Capture != nil {
		t.Fatalf("invalid capture retained: %#v", converted.Capture)
	}
	if !slices.Contains(converted.Anomalies, sensors.AnomalyInvalidCapture) || string(converted.Frame) != "overview" {
		t.Fatalf("legacy telemetry or anomaly missing: %#v", converted)
	}
}

func TestGatewayRejectsLiveAdapterIdentityTakeover(t *testing.T) {
	_, listener, ca, ctx := startGateway(t)
	robotCert := issue(t, ca, "robot-1", false)
	connection := dial(t, listener, robotCert)
	client := fleetv1.NewFleetGatewayClient(connection)

	if _, err := client.Register(ctx, &fleetv1.RegisterRequest{
		RobotId: "robot-1", Adapter: "robocasa", ProtocolVersion: "fleet.v1",
	}); err != nil {
		t.Fatal(err)
	}
	_, err := client.Register(ctx, &fleetv1.RegisterRequest{
		RobotId: "robot-1", Adapter: "mujoco", ProtocolVersion: "fleet.v1",
	})
	if status.Code(err) != codes.FailedPrecondition {
		t.Fatalf("adapter takeover error=%v code=%s", err, status.Code(err))
	}
}

func TestGatewayRejectsCNMismatch(t *testing.T) {
	_, listener, ca, ctx := startGateway(t)
	robotCert := issue(t, ca, "robot-9", false) // CN does not match requested id
	connection := dial(t, listener, robotCert)
	client := fleetv1.NewFleetGatewayClient(connection)
	_, err := client.Register(ctx, &fleetv1.RegisterRequest{RobotId: "robot-1"})
	if err == nil {
		t.Fatal("register with mismatched CN must fail")
	}
}

func TestGatewayRejectsLinkIdentityDifferentFromCertificate(t *testing.T) {
	_, listener, ca, ctx := startGateway(t)
	robotCert := issue(t, ca, "robot-1", false)
	connection := dial(t, listener, robotCert)
	client := fleetv1.NewFleetGatewayClient(connection)
	if _, err := client.Register(ctx, &fleetv1.RegisterRequest{RobotId: "robot-1"}); err != nil {
		t.Fatal(err)
	}
	stream, err := client.Link(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&fleetv1.LinkMessage{Sequence: 1, Payload: &fleetv1.LinkMessage_Heartbeat{Heartbeat: &fleetv1.Heartbeat{RobotId: "robot-2"}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := stream.Recv(); err == nil {
		t.Fatal("Link must reject a heartbeat identity different from the client certificate")
	}
}

func TestGatewayRejectsLinkBeforeRegistration(t *testing.T) {
	_, listener, ca, ctx := startGateway(t)
	robotCert := issue(t, ca, "robot-1", false)
	connection := dial(t, listener, robotCert)
	client := fleetv1.NewFleetGatewayClient(connection)
	stream, err := client.Link(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&fleetv1.LinkMessage{Sequence: 1, Payload: &fleetv1.LinkMessage_Heartbeat{Heartbeat: &fleetv1.Heartbeat{RobotId: "robot-1"}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := stream.Recv(); err == nil {
		t.Fatal("Link must require a completed registration")
	}
}

func TestGatewayPushCommandReachesSession(t *testing.T) {
	server, listener, ca, ctx := startGateway(t)
	robotCert := issue(t, ca, "robot-1", false)
	connection := dial(t, listener, robotCert)
	client := fleetv1.NewFleetGatewayClient(connection)
	if _, err := client.Register(ctx, &fleetv1.RegisterRequest{RobotId: "robot-1"}); err != nil {
		t.Fatal(err)
	}
	stream, err := client.Link(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err := stream.Send(&fleetv1.LinkMessage{Sequence: 1, Payload: &fleetv1.LinkMessage_Heartbeat{Heartbeat: &fleetv1.Heartbeat{RobotId: "robot-1", SentUnixMs: time.Now().UnixMilli()}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := stream.Recv(); err != nil { // consume the heartbeat ack
		t.Fatal(err)
	}
	if err := server.PushCommand("robot-1", "emergency_stop", map[string]string{"reason": "drill"}); err != nil {
		t.Fatalf("push command: %v", err)
	}
	received, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	command := received.GetCommand()
	if command == nil || command.Type != "emergency_stop" || command.Args["reason"] != "drill" {
		t.Fatalf("received = %+v", received)
	}
}

func validFleetCapture() *fleetv1.SensorCapture {
	rgb := []byte("rgb-frame")
	depth := []byte("depth-frame")
	return &fleetv1.SensorCapture{
		SchemaVersion: sensors.SchemaVersionV1, CaptureId: "capture-1", RobotId: "robot1", EpisodeId: "episode-1",
		SimulationStep: 9, SourceSequence: 3, CapturedUnixMs: time.Unix(1_700_000_000, 0).UnixMilli(),
		FrameId: "robot1/rgbd_head", TransformRevision: "transform-1", WorldRevision: 2,
		Frames: []*fleetv1.SensorFrame{
			{SensorId: "robot1/rgbd_head", Modality: sensors.ModalityRGB, MediaType: "image/jpeg", Width: 64, Height: 48,
				Sha256: gatewayDigest(rgb), Intrinsics: gatewayIdentity3(), CameraToWorld: gatewayIdentity4(), Data: rgb},
			{SensorId: "robot1/rgbd_head", Modality: sensors.ModalityDepth, MediaType: "application/x-depth-f32", Width: 64, Height: 48,
				Sha256: gatewayDigest(depth), DepthScaleM: 1, MinRangeM: 0.05, MaxRangeM: 5,
				Intrinsics: gatewayIdentity3(), CameraToWorld: gatewayIdentity4(), Data: depth},
		},
	}
}

func gatewayDigest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func gatewayIdentity3() []float64 { return []float64{1, 0, 0, 0, 1, 0, 0, 0, 1} }
func gatewayIdentity4() []float64 {
	return []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
}
