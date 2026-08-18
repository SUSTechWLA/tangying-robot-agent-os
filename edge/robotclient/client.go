package robotclient

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
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
	connection *grpc.ClientConn
	robot      robotv1.RobotGatewayClient
	profile    string
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
	return &Client{connection: connection, robot: robotv1.NewRobotGatewayClient(connection), profile: profile}, nil
}

func (c *Client) Close() error { return c.connection.Close() }

// Snapshot returns the Robot Runtime capability view. It is the Agent-facing
// boundary; callers do not need to know that this is backed by the Robot
// Gateway gRPC contract.
func (c *Client) Snapshot(ctx context.Context) (runtime.Snapshot, error) {
	capabilities, err := c.robot.GetCapabilities(ctx, &robotv1.GetCapabilitiesRequest{})
	if err != nil {
		return runtime.Snapshot{}, err
	}
	return snapshotFromProto(capabilities), nil
}

// Telemetry returns one low-rate user-observable snapshot: robot identity,
// semantic activity and the last grounded scene/sensor-derived state.
func (c *Client) Telemetry(ctx context.Context, taskID string) (telemetry.Snapshot, error) {
	runtimeSnapshot, err := c.Snapshot(ctx)
	if err != nil {
		return telemetry.Snapshot{}, err
	}
	stream, err := c.robot.Observe(ctx, &robotv1.ObserveRequest{Streams: []string{"entities"}, MaxRateHz: 1})
	if err != nil {
		return telemetry.Snapshot{}, err
	}
	observation, err := stream.Recv()
	if err != nil {
		return telemetry.Snapshot{}, err
	}
	return observationToTelemetry(runtimeSnapshot, observation, taskID), nil
}

func observationToTelemetry(
	runtimeSnapshot runtime.Snapshot,
	observation *robotv1.Observation,
	taskID string,
) telemetry.Snapshot {
	snapshot := telemetry.Snapshot{
		SchemaVersion:    "telemetry.v1",
		ObservedAt:       time.Now().UTC(),
		TaskID:           taskID,
		Adapter:          runtimeSnapshot.Adapter,
		RobotID:          runtimeSnapshot.RobotID,
		SoftwareVersion:  runtimeSnapshot.SoftwareVersion,
		Activity:         observation.SemanticState.Activity,
		Mode:             observation.SemanticState.Mode,
		EmergencyStopped: observation.SemanticState.EmergencyStopped,
		Anomalies:        append([]string(nil), observation.SemanticState.Anomalies...),
		LastError:        observation.SemanticState.LastError,
	}
	if observation.RobotState != nil {
		snapshot.RobotState = observation.RobotState.AsMap()
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
	stream, err := c.robot.Observe(ctx, &robotv1.ObserveRequest{Streams: []string{"entities"}, MaxRateHz: 1})
	if err != nil {
		return manipulation.GroundedTask{}, err
	}
	observation, err := stream.Recv()
	if err != nil {
		return manipulation.GroundedTask{}, err
	}
	objects := matchingEntities(observation.Entities, intent.Object)
	destinations := matchingEntities(observation.Entities, intent.Destination)
	if len(objects) != 1 || len(destinations) != 1 {
		return manipulation.GroundedTask{}, fmt.Errorf("grounding ambiguous: objects=%d destinations=%d", len(objects), len(destinations))
	}
	return manipulation.GroundedTask{
		Action:      intent.Action,
		Object:      manipulation.SceneRef{ID: objects[0].EntityId, Confidence: objects[0].Confidence},
		Destination: manipulation.SceneRef{ID: destinations[0].EntityId, Confidence: destinations[0].Confidence},
		KeepUpright: intent.Constraints.KeepUpright,
	}, nil
}

func (c *Client) Execute(ctx context.Context, taskID string, step taskgraph.SkillStep) (runtime.SkillResult, error) {
	parameters, err := structpb.NewStruct(step.Arguments)
	if err != nil {
		return runtime.SkillResult{}, err
	}
	target := stringArgument(step.Arguments, "targetRef")
	if target == "" {
		target = stringArgument(step.Arguments, "destinationId")
	}
	if target == "" {
		target = stringArgument(step.Arguments, "objectId")
	}
	deadline := step.DeadlineUnixMS
	if deadline == 0 {
		deadline = time.Now().Add(30 * time.Second).UnixMilli()
	}
	timeout := time.Until(time.UnixMilli(deadline))
	if timeout <= 0 {
		return runtime.SkillResult{}, runtime.ErrSkillCommandExpired
	}
	executeContext, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	lease := step.LeaseMS
	if lease == 0 {
		lease = 5_000
	}
	commandID, idempotencyKey := commandIdentity(taskID, step)
	stream, err := c.robot.ExecuteSkill(executeContext, &robotv1.SkillCommand{
		SchemaVersion: "robot.v1", CommandId: commandID, TaskId: taskID, Skill: step.Skill,
		TargetRef: target, Parameters: parameters, DeadlineUnixMs: deadline, LeaseMs: lease,
		IdempotencyKey: idempotencyKey, SafetyProfile: c.profile, ApprovalId: step.ApprovalID,
	})
	if err != nil {
		return runtime.SkillResult{}, err
	}
	var terminal *robotv1.SkillEvent
	for {
		event, recvErr := stream.Recv()
		if recvErr != nil {
			if terminal != nil {
				break
			}
			return runtime.SkillResult{}, recvErr
		}
		if isTerminalSkillEvent(event.Type) {
			terminal = event
		}
	}
	if terminal == nil {
		return runtime.SkillResult{}, runtime.ErrSkillStreamClosed
	}
	return runtime.SkillResult{
		Success:                terminal.Type == robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED,
		Code:                   terminal.Code,
		Message:                terminal.Message,
		ObservationID:          terminal.ObservationId,
		VerificationConfidence: terminal.VerificationConfidence,
	}, nil
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

func commandIdentity(taskID string, step taskgraph.SkillStep) (string, string) {
	idempotencyKey := step.IdempotencyKey
	if idempotencyKey == "" {
		idempotencyKey = taskID + ":read:" + step.ID
	}
	return taskID + ":" + step.ID, idempotencyKey
}

func snapshotFromProto(proto *robotv1.RobotCapabilities) runtime.Snapshot {
	snapshot := runtime.Snapshot{
		RobotID:         proto.RobotId,
		Adapter:         proto.Adapter,
		SoftwareVersion: proto.SoftwareVersion,
		Ready:           proto.ManipulationReady,
		Blockers:        append([]string(nil), proto.Blockers...),
	}
	if len(proto.Capabilities) > 0 {
		for _, item := range proto.Capabilities {
			snapshot.Capabilities = append(snapshot.Capabilities, runtime.Capability{
				Name:             item.Name,
				Description:      item.Description,
				SafetyLevel:      item.SafetyLevel,
				Available:        item.Available,
				Blockers:         append([]string(nil), item.Blockers...),
				Cancellable:      item.Cancellable,
				Recoverable:      item.Recoverable,
				DefaultTimeout:   time.Duration(item.DefaultTimeoutMs) * time.Millisecond,
				InputParameters:  append([]string(nil), item.InputParameters...),
				OutputParameters: append([]string(nil), item.OutputParameters...),
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

func stringArgument(arguments map[string]any, key string) string {
	value, _ := arguments[key].(string)
	return value
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
