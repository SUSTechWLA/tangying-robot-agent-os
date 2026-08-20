// edge-worker is one fleet edge worker: it bridges the cloud control plane
// and one local Robot Runtime. Configuration comes from environment
// variables (see docs/fleet-cloud.md).
package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
	fleetv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/fleet/v1"
)

func main() {
	flag.Parse()
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	robotID := envOr("EDGE_ROBOT_ID", "")
	if robotID == "" {
		return errRequired("EDGE_ROBOT_ID")
	}
	fleetURL := envOr("EDGE_FLEET_URL", "")
	if fleetURL == "" {
		return errRequired("EDGE_FLEET_URL")
	}
	deviceToken := envOr("EDGE_DEVICE_TOKEN", "")
	if deviceToken == "" {
		return errRequired("EDGE_DEVICE_TOKEN")
	}

	// Robot Runtime connection (mTLS unless dev-insecure is explicit).
	runtimeInsecure, _ := strconv.ParseBool(envOr("EDGE_RUNTIME_INSECURE", "0"))
	runtimeClient, err := robotclient.New(robotclient.Config{
		Address:     envOr("EDGE_RUNTIME_ADDR", "127.0.0.1:50051"),
		DevInsecure: runtimeInsecure,
		CAFile:      os.Getenv("EDGE_RUNTIME_CA"),
		CertFile:    os.Getenv("EDGE_RUNTIME_CERT"),
		KeyFile:     os.Getenv("EDGE_RUNTIME_KEY"),
		ServerName:  envOr("EDGE_RUNTIME_SERVER_NAME", "localhost"),
	})
	if err != nil {
		return err
	}
	defer runtimeClient.Close()
	runtimeInfo, err := runtimeClient.Info(context.Background())
	if err != nil {
		return err
	}

	// Cloud data plane.
	cloud := cloudclient.New(cloudclient.Config{
		BaseURL:     fleetURL,
		RobotID:     robotID,
		DeviceToken: deviceToken,
		CAFile:      os.Getenv("EDGE_FLEET_CA"),
		ServerName:  envOr("EDGE_FLEET_SERVER_NAME", ""),
	})

	// Task source: direct Redis Stream or HTTP long-poll.
	source, err := buildTaskSource(robotID, cloud)
	if err != nil {
		return err
	}
	if closer, ok := source.(interface{ Close() error }); ok {
		defer closer.Close()
	}

	// Optional mTLS gRPC Link channel (public-network robot access).
	var link *cloudclient.Link
	worldID := envOr("EDGE_WORLD_ID", "fleet-default")
	transformRevision := envOr("EDGE_TRANSFORM_REVISION", envOr("EDGE_ADAPTER", "mujoco")+"-world-v1")
	if gatewayAddr := os.Getenv("EDGE_FLEET_GRPC"); gatewayAddr != "" {
		capabilities, tools := toolAdvertisements(runtimeInfo)
		observationRevision, observationSources, catalogErr := observationAdvertisements(
			robotID, envOr("EDGE_ADAPTER", "mujoco"), runtimeInfo.AdapterVersion, transformRevision,
		)
		if catalogErr != nil {
			return catalogErr
		}
		linkConfig := cloudclient.LinkConfig{
			Address:                    gatewayAddr,
			CAFile:                     os.Getenv("EDGE_MTLS_CA"),
			CertFile:                   os.Getenv("EDGE_MTLS_CERT"),
			KeyFile:                    os.Getenv("EDGE_MTLS_KEY"),
			ServerName:                 envOr("EDGE_MTLS_SERVER_NAME", "localhost"),
			RobotID:                    robotID,
			Adapter:                    envOr("EDGE_ADAPTER", "mujoco"),
			AdapterVersion:             runtimeInfo.AdapterVersion,
			Capabilities:               capabilities,
			ToolCatalogRevision:        runtimeInfo.CatalogRevision,
			ToolCatalog:                tools,
			ObservationCatalogRevision: observationRevision,
			ObservationSources:         observationSources,
		}
		if interval, parseErr := time.ParseDuration(os.Getenv("EDGE_HEARTBEAT_INTERVAL")); parseErr == nil {
			linkConfig.HeartbeatInterval = interval
		}
		if lease, parseErr := time.ParseDuration(os.Getenv("EDGE_LEASE")); parseErr == nil {
			linkConfig.Lease = lease
		}
		link, err = cloudclient.NewLink(linkConfig, nil)
		if err != nil {
			return err
		}
	}

	worldPose, _ := parseFloats(os.Getenv("EDGE_WORLD_POSE"), 4)
	observationSequenceBase := uint64(time.Now().UTC().UnixMilli()) * 1_000_000
	if configured := strings.TrimSpace(os.Getenv("EDGE_OBSERVATION_SEQUENCE_BASE")); configured != "" {
		parsed, parseErr := strconv.ParseUint(configured, 10, 64)
		if parseErr != nil {
			return parseErr
		}
		observationSequenceBase = parsed
	}

	workerInstance := worker.New(worker.Config{
		RobotID:                 robotID,
		Adapter:                 envOr("EDGE_ADAPTER", "mujoco"),
		Source:                  source,
		Cloud:                   cloud,
		Link:                    link,
		Runtime:                 runtimeClient,
		WorldID:                 worldID,
		TransformRevision:       transformRevision,
		AdapterVersion:          runtimeInfo.AdapterVersion,
		ObservationSequenceBase: observationSequenceBase,
		WorldPose:               worldPose,
		TelemetryInterval:       envDuration("EDGE_TELEMETRY_INTERVAL", 2*time.Second),
	})

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	if link != nil {
		link.SetCommandHandler(workerInstance.HandleCommand)
		go link.Run(ctx)
		defer link.Close()
	}

	log.Printf("edge-worker %s: fleet=%s runtime=%s source=%s link=%s",
		robotID, fleetURL, envOr("EDGE_RUNTIME_ADDR", "127.0.0.1:50051"),
		envOr("EDGE_TASK_SOURCE", "http"), boolString(link != nil))
	return workerInstance.Run(ctx)
}

func toolAdvertisements(snapshot runtime.Snapshot) ([]*fleetv1.Capability, []*fleetv1.ToolDescriptor) {
	capabilities := make([]*fleetv1.Capability, 0, len(snapshot.Capabilities))
	tools := make([]*fleetv1.ToolDescriptor, 0, len(snapshot.Capabilities))
	for _, capability := range snapshot.Capabilities {
		capabilities = append(capabilities, &fleetv1.Capability{Name: capability.Name, Available: capability.Available})
		sideEffect := "read_only"
		if capability.Name == string(runtime.CapabilityEmergencyStop) || capability.Name == "emergency_stop" {
			sideEffect = "emergency"
		} else if capability.SafetyLevel == "physical_motion" {
			sideEffect = "physical_atomic"
		}
		tools = append(tools, &fleetv1.ToolDescriptor{
			Name: capability.Name, Description: capability.Description,
			InputParameters: append([]string(nil), capability.InputParameters...), OutputParameters: append([]string(nil), capability.OutputParameters...),
			SideEffectClass: sideEffect, SafetyLevel: capability.SafetyLevel, Available: capability.Available,
		})
	}
	return capabilities, tools
}

func observationAdvertisements(robotID, adapter, adapterVersion, transformRevision string) (string, []*fleetv1.ObservationSource, error) {
	sceneSourceType := observation.SourceRGBDCamera
	if isSimulationAdapter(adapter) {
		sceneSourceType = observation.SourceSimGroundTruth
	}
	descriptors := []observation.SourceDescriptor{
		{
			SourceID: robotID + "/proprioception", SourceType: observation.SourceProprioception,
			SchemaRevision: "world.observation.v1", Kind: observation.RobotStateUpsert,
			FrameID: "world", FrameIDs: []string{"base", "world"}, TransformRevision: transformRevision,
			MaxRateHz: 20, MaxAge: 500 * time.Millisecond, AdapterVersion: adapterVersion, Required: true,
		},
		{
			SourceID: robotID + "/scene", SourceType: sceneSourceType,
			SchemaRevision: "world.observation.v1", Kind: observation.EntityUpsert,
			FrameID: "world", FrameIDs: []string{"camera", "world"}, TransformRevision: transformRevision,
			MaxRateHz: 20, MaxAge: time.Second, AdapterVersion: adapterVersion, Required: true,
		},
	}
	revision, err := observation.CatalogRevision(descriptors)
	if err != nil {
		return "", nil, err
	}
	sources := make([]*fleetv1.ObservationSource, 0, len(descriptors))
	for _, descriptor := range descriptors {
		sources = append(sources, &fleetv1.ObservationSource{
			SourceId: descriptor.SourceID, SourceType: string(descriptor.SourceType), SchemaRevision: descriptor.SchemaRevision,
			FrameIds: append([]string(nil), descriptor.FrameIDs...), TransformRevision: descriptor.TransformRevision,
			UpdateRateHz: descriptor.MaxRateHz, FreshnessBudgetMs: uint32(descriptor.MaxAge.Milliseconds()),
			PayloadKinds: []string{string(descriptor.Kind)}, AdapterVersion: descriptor.AdapterVersion,
		})
	}
	return revision, sources, nil
}

func isSimulationAdapter(adapter string) bool {
	switch strings.ToLower(strings.TrimSpace(adapter)) {
	case "mujoco", "robocasa":
		return true
	default:
		return false
	}
}

func buildTaskSource(robotID string, cloud *cloudclient.Client) (worker.TaskSource, error) {
	switch envOr("EDGE_TASK_SOURCE", "http") {
	case "redis":
		addr := envOr("REDIS_ADDR", "")
		if addr == "" {
			return nil, errRequired("REDIS_ADDR (for EDGE_TASK_SOURCE=redis)")
		}
		return worker.NewRedisTaskSource(addr, os.Getenv("REDIS_PASSWORD"),
			envOr("REDIS_STREAM", "fleet.tasks.ready"), envOr("REDIS_GROUP", "fleet-edge"), robotID, 0)
	default:
		return worker.NewHTTPTaskSource(cloud, robotID), nil
	}
}

func parseFloats(raw string, expected int) ([]float64, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	parts := strings.Split(raw, ",")
	if len(parts) > expected {
		parts = parts[:expected]
	}
	result := make([]float64, 0, len(parts))
	for _, part := range parts {
		value, err := strconv.ParseFloat(strings.TrimSpace(part), 64)
		if err != nil {
			return nil, err
		}
		result = append(result, value)
	}
	return result, nil
}

func envDuration(key string, fallback time.Duration) time.Duration {
	value, err := time.ParseDuration(os.Getenv(key))
	if err != nil {
		return fallback
	}
	return value
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func boolString(value bool) string {
	if value {
		return "mTLS"
	}
	return "disabled"
}

func errRequired(key string) error {
	return &configError{key: key}
}

type configError struct{ key string }

func (e *configError) Error() string {
	return "required environment variable " + e.key + " is not set"
}
