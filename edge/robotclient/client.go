package robotclient

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/types/known/structpb"
)

type Config struct {
	Address     string
	DevInsecure bool
	CAFile      string
	CertFile    string
	KeyFile     string
	ServerName  string
	Profile     string
}

type Client struct {
	connection      *grpc.ClientConn
	robot           robotv1.RobotRuntimeClient
	profile         string
	profileExplicit bool
	contractMu      sync.Mutex
	profileDigest   string
	captures        map[string]captureCursor
}

func New(config Config) (*Client, error) {
	if config.Address == "" {
		return nil, errors.New("robot gateway address is required")
	}
	var option grpc.DialOption
	if config.DevInsecure {
		option = grpc.WithTransportCredentials(insecure.NewCredentials())
	} else {
		transport, err := tlsCredentials(config)
		if err != nil {
			return nil, err
		}
		option = grpc.WithTransportCredentials(transport)
	}
	connection, err := grpc.NewClient(config.Address, option)
	if err != nil {
		return nil, err
	}
	profile := config.Profile
	if profile == "" && config.DevInsecure {
		profile = "simulation"
	}
	if profile == "" {
		profile = "desktop_standard"
	}
	return &Client{connection: connection, robot: robotv1.NewRobotRuntimeClient(connection), profile: profile, profileExplicit: config.Profile != ""}, nil
}

func (c *Client) Close() error { return c.connection.Close() }

// Info returns the Robot Runtime capability view. It is the Agent-facing
// boundary; callers do not need to know that this is backed by the Robot
// Gateway gRPC contract.
func (c *Client) Info(ctx context.Context) (runtime.Snapshot, error) {
	capabilities, err := c.robot.GetRuntimeInfo(ctx, &robotv1.GetRuntimeInfoRequest{})
	if err != nil {
		return runtime.Snapshot{}, err
	}
	snapshot := snapshotFromProto(capabilities)
	if err := c.acceptProfile(capabilities, &snapshot); err != nil {
		return runtime.Snapshot{}, err
	}
	if err := snapshot.ValidateProtocol("1.0"); err != nil {
		return runtime.Snapshot{}, err
	}
	return snapshot, nil
}

// Telemetry returns one low-rate user-observable snapshot: robot identity,
// semantic activity and the last grounded scene/sensor-derived state.
func (c *Client) Telemetry(ctx context.Context, taskID string) (telemetry.Snapshot, error) {
	return c.TelemetrySource(ctx, taskID, "")
}

// TelemetrySource captures exactly one declared sensor; image bytes and point
// cloud share the same response rather than two independent camera polls.
func (c *Client) TelemetrySource(ctx context.Context, taskID, sourceID string) (telemetry.Snapshot, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	runtimeSnapshot, err := c.Info(ctx)
	if err != nil {
		return telemetry.Snapshot{}, err
	}
	if sourceID != "" {
		if runtimeSnapshot.RobotProfile == nil {
			return telemetry.Snapshot{}, errors.New("camera source requires a declared profile")
		}
		sensor, exists := runtimeSnapshot.RobotProfile.Sensor(sourceID)
		if !exists || sensor.SourceType != "rgbd_camera" {
			return telemetry.Snapshot{}, errors.New("camera source not declared as RGB-D")
		}
	}
	stream, err := c.robot.Observe(ctx, &robotv1.ObserveRequest{Streams: []string{"entities", "rgb", "depth", "reconstruction", "robot_state"}, MaxRateHz: 1, SourceId: sourceID})
	if err != nil {
		return telemetry.Snapshot{}, err
	}
	observation, err := stream.Recv()
	if err != nil {
		return telemetry.Snapshot{}, err
	}
	if sourceID != "" && observation.GetReconstruction().GetFields()["sourceId"].GetStringValue() != sourceID {
		return telemetry.Snapshot{}, errors.New("runtime returned a different camera source")
	}
	reconstruction, err := c.acceptReconstruction(runtimeSnapshot, observation)
	if err != nil {
		return telemetry.Snapshot{}, err
	}
	snapshot := observationToTelemetry(runtimeSnapshot, observation, taskID)
	snapshot.RobotProfile = runtimeSnapshot.RobotProfile
	snapshot.Reconstruction = reconstruction
	return snapshot, nil
}

func observationToTelemetry(
	runtimeSnapshot runtime.Snapshot,
	observation *robotv1.Observation,
	taskID string,
) telemetry.Snapshot {
	semanticState := observation.SemanticState
	if semanticState == nil {
		semanticState = &robotv1.SemanticState{}
	}
	snapshot := telemetry.Snapshot{
		SchemaVersion:       "telemetry.v1",
		TaskID:              taskID,
		Adapter:             runtimeSnapshot.Adapter,
		RobotID:             runtimeSnapshot.RobotID,
		SoftwareVersion:     runtimeSnapshot.SoftwareVersion,
		Activity:            semanticState.Activity,
		Mode:                semanticState.Mode,
		EmergencyStopped:    semanticState.EmergencyStopped,
		Anomalies:           append([]string(nil), semanticState.Anomalies...),
		LastError:           semanticState.LastError,
		Frame:               append([]byte(nil), observation.CompressedImage...),
		FrameMediaType:      observation.ImageMediaType,
		DepthFrame:          append([]byte(nil), observation.CompressedDepthImage...),
		DepthFrameMediaType: observation.DepthImageMediaType,
	}
	if observation.RobotState != nil {
		snapshot.RobotState = observation.RobotState.AsMap()
	}
	if observation.WallTimeUnixMs > 0 {
		snapshot.ObservedAt = time.UnixMilli(observation.WallTimeUnixMs).UTC()
	}
	for _, entity := range observation.Entities {
		snapshot.Entities = append(snapshot.Entities, telemetry.Entity{
			EntityID:   entity.EntityId,
			Category:   entity.Category,
			Attributes: entity.Attributes,
			Pose:       append([]float64(nil), entity.PoseXyzQuat...),
			Confidence: entity.Confidence,
			Relation:   entity.Relation,
		})
	}
	return snapshot
}

func (c *Client) Ground(ctx context.Context, intent manipulation.Intent) (manipulation.GroundedTask, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	info, err := c.Info(ctx)
	if err != nil {
		return manipulation.GroundedTask{}, err
	}
	if intent.Action == manipulation.ActionHomeRoute {
		if capability, mobile := info.Capability("navigation.navigate"); !mobile || !capability.Available {
			return manipulation.GroundedTask{}, errors.New("home route requires a mobile navigation capability")
		}
		goals, err := manipulation.HomeRouteGoals(intent.RouteRooms)
		if err != nil {
			return manipulation.GroundedTask{}, err
		}
		return manipulation.GroundedTask{
			Action: intent.Action, RouteRooms: append([]string(nil), intent.RouteRooms...),
			RouteGoals: goals, ReturnToStart: intent.ReturnToStart,
		}, nil
	}
	if intent.Action == manipulation.ActionHomeManipulation {
		if capability, mobile := info.Capability("navigation.navigate"); !mobile || !capability.Available {
			return manipulation.GroundedTask{}, errors.New("home manipulation requires a mobile navigation capability")
		}
		goals, err := manipulation.HomeRouteGoals(intent.RouteRooms)
		if err != nil {
			return manipulation.GroundedTask{}, err
		}
		objectID, err := homeObjectID(intent.Object)
		if err != nil {
			return manipulation.GroundedTask{}, err
		}
		destinationID, err := homeDestinationID(intent.Destination)
		if err != nil {
			return manipulation.GroundedTask{}, err
		}
		return manipulation.GroundedTask{
			Action: intent.Action, Object: manipulation.SceneRef{ID: objectID, Confidence: 0.90},
			Destination: manipulation.SceneRef{ID: destinationID, Confidence: 0.90},
			KeepUpright: intent.Constraints.KeepUpright,
			RouteRooms:  append([]string(nil), intent.RouteRooms...), RouteGoals: goals,
			ReturnToStart: intent.ReturnToStart,
		}, nil
	}
	stream, err := c.robot.Observe(ctx, &robotv1.ObserveRequest{Streams: []string{"entities", "reconstruction"}, MaxRateHz: 1})
	if err != nil {
		return manipulation.GroundedTask{}, err
	}
	observation, err := stream.Recv()
	if err != nil {
		return manipulation.GroundedTask{}, err
	}
	if _, err := c.acceptReconstruction(info, observation); err != nil {
		return manipulation.GroundedTask{}, err
	}
	objects := matchingEntities(observation.Entities, intent.Object)
	destinations := matchingEntities(observation.Entities, intent.Destination)
	if len(objects) != 1 || len(destinations) != 1 {
		return manipulation.GroundedTask{}, fmt.Errorf("grounding %s: objects=%d destinations=%d%s",
			groundingOutcome(len(objects), len(destinations)), len(objects), len(destinations),
			groundingContext(info, observation))
	}
	if intent.Source.Category != "" {
		sources := matchingEntities(observation.Entities, intent.Source)
		if len(sources) != 1 {
			return manipulation.GroundedTask{}, fmt.Errorf("grounding source %s: sources=%d%s",
				groundingOutcome(len(sources)), len(sources), groundingContext(info, observation))
		}
		relation := objects[0].Relation
		if relation != "inside:"+sources[0].EntityId && relation != "on:"+sources[0].EntityId {
			return manipulation.GroundedTask{}, fmt.Errorf("grounding source mismatch: object %q is not observed inside/on source %q", objects[0].EntityId, sources[0].EntityId)
		}
	}
	goal, err := navigationGoal(info, observation)
	if err != nil {
		return manipulation.GroundedTask{}, err
	}
	return manipulation.GroundedTask{
		Action:         intent.Action,
		Object:         manipulation.SceneRef{ID: objects[0].EntityId, Confidence: objects[0].Confidence},
		Destination:    manipulation.SceneRef{ID: destinations[0].EntityId, Confidence: destinations[0].Confidence},
		KeepUpright:    intent.Constraints.KeepUpright,
		NavigationGoal: goal,
	}, nil
}

func homeObjectID(selector manipulation.EntitySelector) (string, error) {
	color := strings.TrimSpace(selector.Attributes["color"])
	if selector.Category == "cup" && color == "red" {
		return "red-cup", nil
	}
	return "", fmt.Errorf("home task object is not commissioned: %s/%s", color, selector.Category)
}

func homeDestinationID(selector manipulation.EntitySelector) (string, error) {
	if selector.Category == manipulation.CategoryStorageBin && selector.Attributes["color"] == "blue" {
		return "kitchen-bin", nil
	}
	return "", fmt.Errorf("home task destination is not commissioned: %s/%s", selector.Attributes["color"], selector.Category)
}

func navigationGoal(info runtime.Snapshot, observation *robotv1.Observation) ([]float64, error) {
	capability, mobile := info.Capability("navigation.navigate")
	if !mobile {
		return nil, nil
	}
	state := observation.RobotState.AsMap()
	if _, commissioned := state["navigation"]; !commissioned && !capability.Available {
		// Sensor-only adapters can declare an unavailable future base tool.
		// Reading/grounding their observations must not require arming motors.
		// An active navigation deployment always declares its navigation state.
		return nil, nil
	}
	if info.RobotProfile == nil {
		return nil, errors.New("navigation requires explicit robot profile workspace limits")
	}
	if err := info.CanExecute("navigation.navigate"); err != nil {
		return nil, err
	}
	navigation, _ := state["navigation"].(map[string]any)
	values, _ := navigation["approach_goal_pose"].([]any)
	goal := make([]float64, len(values))
	for i, v := range values {
		number, ok := v.(float64)
		if !ok {
			return nil, errors.New("invalid navigation approach goal")
		}
		goal[i] = number
	}
	if !robotcontract.ValidPose(goal) {
		return nil, errors.New("mobile adapter omitted a valid navigation approach goal")
	}
	for i, key := range []string{"navigation.x", "navigation.y", "navigation.z"} {
		limit, ok := info.RobotProfile.ActionLimits[key]
		if !ok || goal[i] < limit.Min || goal[i] > limit.Max {
			return nil, errors.New("navigation approach goal exceeds commissioned workspace")
		}
	}
	return goal, nil
}

func (c *Client) Invoke(ctx context.Context, command runtime.Command) (runtime.Result, error) {
	startedAt := time.Now()
	defaultProfile := c.profile
	if !c.profileExplicit && command.SafetyProfile == "" {
		info, err := c.Info(ctx)
		if err != nil {
			return runtime.Result{}, err
		}
		// Plaintext is a transport choice, not evidence that a new adapter
		// supports the legacy simulator's safety profile.
		if info.RobotProfile != nil {
			defaultProfile = "desktop_standard"
		}
	}
	request, err := commandToProto(command, defaultProfile)
	if err != nil {
		return runtime.Result{}, err
	}
	timeout := time.Until(command.Deadline)
	if timeout <= 0 {
		return runtime.Result{}, runtime.ErrSkillCommandExpired
	}
	executeContext, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	stream, err := c.robot.ExecuteSkill(executeContext, request)
	if err != nil {
		return runtime.Result{}, err
	}
	var terminal *robotv1.SkillEvent
	for {
		event, recvErr := stream.Recv()
		if recvErr != nil {
			if terminal != nil {
				break
			}
			return runtime.Result{}, recvErr
		}
		if isTerminalSkillEvent(event.Type) {
			terminal = event
		}
	}
	if terminal == nil {
		return runtime.Result{}, runtime.ErrSkillStreamClosed
	}
	result := runtime.Result{
		Success:                terminal.Type == robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED,
		Code:                   terminal.Code,
		Message:                terminal.Message,
		ObservationID:          terminal.ObservationId,
		VerificationConfidence: terminal.VerificationConfidence,
	}
	if captured := terminal.EvidenceObservation; captured != nil {
		if terminal.CommandId != request.CommandId || captured.ObservationId != terminal.ObservationId || captured.ObservationId == "" {
			return runtime.Result{}, errors.New("command evidence identity does not match result")
		}
		if captured.WallTimeUnixMs < startedAt.Add(-250*time.Millisecond).UnixMilli() {
			return runtime.Result{}, errors.New("command evidence predates this invocation")
		}
		info, err := c.Info(ctx)
		if err != nil {
			return runtime.Result{}, err
		}
		scene, err := validateReconstruction(info, captured)
		if err != nil {
			return runtime.Result{}, err
		}
		if scene == nil {
			return runtime.Result{}, errors.New("command evidence requires a declared sensor profile")
		}
		c.contractMu.Lock()
		previous, seen := c.captures[scene.SourceID]
		changed := seen && (scene.Sequence == previous.sequence || scene.ObservationID == previous.id) && digest(scene) != previous.digest
		c.contractMu.Unlock()
		if changed {
			return runtime.Result{}, errors.New("immutable command evidence changed")
		}
		snapshot := observationToTelemetry(info, captured, command.TaskID)
		snapshot.RobotProfile, snapshot.Reconstruction = info.RobotProfile, scene
		result.Evidence = &snapshot
	}
	return result, nil
}

// Cancel asks the Robot Runtime to cancel an in-flight capability invocation.
// It is intentionally separate from EmergencyStop: cancel is a controlled
// stop of one task, not a latched safety stop.
func (c *Client) Cancel(ctx context.Context, commandID, reason string) (bool, error) {
	result, err := c.robot.Cancel(ctx, &robotv1.CancelRequest{CommandId: commandID, Reason: reason})
	if err != nil {
		return false, err
	}
	return result.Accepted && result.State == "CANCELLED", nil
}

// EmergencyStop latches the Robot Runtime safety stop. The LLM/Agent cannot
// clear it through this API; clearing requires local operator action.
func (c *Client) EmergencyStop(ctx context.Context, reason string) error {
	_, err := c.robot.EmergencyStop(ctx, &robotv1.EStopRequest{Reason: reason})
	return err
}

func isTerminalSkillEvent(eventType robotv1.SkillEventType) bool {
	switch eventType {
	case robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED,
		robotv1.SkillEventType_SKILL_EVENT_FAILED,
		robotv1.SkillEventType_SKILL_EVENT_SAFETY_STOPPED,
		robotv1.SkillEventType_SKILL_EVENT_CANCELLED:
		return true
	default:
		return false
	}
}

func commandToProto(command runtime.Command, defaultProfile string) (*robotv1.SkillCommand, error) {
	parameters := &structpb.Struct{}
	if command.Parameters != nil {
		// Plans contain typed slices (e.g. []float64 goal poses). Encode through
		// the JSON wire contract instead of NewStruct's []any-only type switch.
		encoded, err := json.Marshal(command.Parameters)
		if err != nil {
			return nil, fmt.Errorf("invalid command parameters: %w", err)
		}
		if err := parameters.UnmarshalJSON(encoded); err != nil {
			return nil, fmt.Errorf("invalid command parameters: %w", err)
		}
	}
	leaseMilliseconds := command.Lease.Milliseconds()
	if leaseMilliseconds < 0 || leaseMilliseconds > int64(^uint32(0)) {
		return nil, fmt.Errorf("invalid command lease: %s", command.Lease)
	}
	schemaVersion := command.SchemaVersion
	if schemaVersion == "" {
		schemaVersion = "robot.v1"
	}
	profile := command.SafetyProfile
	if profile == "" {
		profile = defaultProfile
	}
	return &robotv1.SkillCommand{
		SchemaVersion:      schemaVersion,
		CommandId:          command.CommandID,
		TaskId:             command.TaskID,
		Skill:              string(command.Capability),
		TargetRef:          command.TargetRef,
		Parameters:         parameters,
		DeadlineUnixMs:     command.Deadline.UnixMilli(),
		LeaseMs:            uint32(leaseMilliseconds),
		IdempotencyKey:     command.IdempotencyKey,
		SafetyProfile:      profile,
		ApprovalId:         command.ApprovalID,
		RobotId:            command.RobotID,
		CatalogRevision:    command.CatalogRevision,
		WorldRevisionBasis: command.WorldRevisionBasis,
		ResourceId:         command.ResourceID,
		FencingToken:       command.FencingToken,
		TaskRevision:       command.TaskRevision,
		AggregateVersion:   command.AggregateVersion,
		StepId:             command.StepID,
	}, nil
}

func snapshotFromProto(proto *robotv1.RuntimeInfo) runtime.Snapshot {
	snapshot := runtime.Snapshot{
		RobotID:         proto.RobotId,
		Adapter:         proto.Adapter,
		AdapterVersion:  proto.AdapterVersion,
		CatalogRevision: proto.CatalogRevision,
		SoftwareVersion: proto.SoftwareVersion,
		ProtocolVersion: proto.ProtocolVersion,
		RuntimeVersion:  proto.RuntimeVersion,
		Ready:           proto.ManipulationReady,
		Blockers:        append([]string(nil), proto.Blockers...),
	}
	if len(proto.Capabilities) > 0 {
		for _, item := range proto.Capabilities {
			snapshot.Capabilities = append(snapshot.Capabilities, runtime.Capability{
				Name:              item.Name,
				Description:       item.Description,
				DisplayName:       item.DisplayName,
				Purpose:           item.Purpose,
				SafetyLevel:       item.SafetyLevel,
				Available:         item.Available,
				Blockers:          append([]string(nil), item.Blockers...),
				Cancellable:       item.Cancellable,
				Recoverable:       item.Recoverable,
				DefaultTimeout:    time.Duration(item.DefaultTimeoutMs) * time.Millisecond,
				InputParameters:   append([]string(nil), item.InputParameters...),
				OutputParameters:  append([]string(nil), item.OutputParameters...),
				SafeArgumentNames: append([]string(nil), item.SafeArgumentNames...),
				MutatesWorld:      item.MutatesWorld,
			})
		}
		return snapshot
	}
	// Backward compatibility with robot gateways that only report the flat
	// skills list. Those entries are treated as currently available.
	for _, skill := range proto.Skills {
		snapshot.Capabilities = append(snapshot.Capabilities, runtime.Capability{
			Name:      skill,
			Available: true,
		})
	}
	return snapshot
}

// groundingOutcome separates three operator situations that a bare count
// cannot: nothing matched at all (absent), more than one candidate matched
// (ambiguous, which needs a narrower request), and a reference that did not
// resolve. Reconciliation policy depends on the difference.
func groundingOutcome(counts ...int) string {
	outcome := "absent"
	for _, count := range counts {
		if count > 1 {
			return "ambiguous"
		}
	}
	return outcome
}

// groundingContext explains a failed grounding from the same observation that
// failed. A collision count alone cannot distinguish "the robot's camera sees
// something else" from "this scene commissions no such object", and those need
// different operator actions, so name the observed scene and the real override.
func groundingContext(info runtime.Snapshot, observation *robotv1.Observation) string {
	described := make([]string, 0, len(observation.Entities))
	for _, entity := range observation.Entities {
		label := entity.Category
		if label == "" {
			label = "unknown"
		}
		if colour := entity.Attributes["color"]; colour != "" {
			label += "/" + colour
		}
		described = append(described, fmt.Sprintf("%s(%s)", entity.EntityId, label))
	}
	sort.Strings(described)
	robot := info.RobotID
	if robot == "" {
		robot = "unknown-robot"
	}
	context := fmt.Sprintf("; robot=%s adapter=%s observed=%d", robot, info.Adapter, len(described))
	if len(described) == 0 {
		return context + "; this scene commissions no pickable objects, check the camera frame and the runtime scene selection (simulation: scripts/sim-stack.sh restart --perception rgbd --scene tabletop)"
	}
	return context + "; visible=" + strings.Join(described, ",")
}

func matchingEntities(entities []*robotv1.SceneEntity, selector manipulation.EntitySelector) []*robotv1.SceneEntity {
	var result []*robotv1.SceneEntity
	for _, entity := range entities {
		if entity.Category != selector.Category || (selector.Relation != "" && entity.Relation != selector.Relation) {
			continue
		}
		matches := true
		for key, value := range selector.Attributes {
			if value != "" && entity.Attributes[key] != value {
				matches = false
			}
		}
		if matches {
			result = append(result, entity)
		}
	}
	return result
}

func tlsCredentials(config Config) (credentials.TransportCredentials, error) {
	if config.CAFile == "" || config.CertFile == "" || config.KeyFile == "" {
		return nil, errors.New("CA, certificate, and key files are required unless DevInsecure is enabled")
	}
	caBytes, err := os.ReadFile(config.CAFile)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caBytes) {
		return nil, errors.New("invalid CA certificate")
	}
	certificate, err := tls.LoadX509KeyPair(config.CertFile, config.KeyFile)
	if err != nil {
		return nil, err
	}
	return credentials.NewTLS(&tls.Config{
		MinVersion: tls.VersionTLS13, RootCAs: roots, Certificates: []tls.Certificate{certificate}, ServerName: config.ServerName,
	}), nil
}
