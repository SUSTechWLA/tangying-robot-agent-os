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
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
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
		Object:      manipulation.SceneRef{ID: objects[0].EntityId, Confidence: objects[0].Confidence},
		Destination: manipulation.SceneRef{ID: destinations[0].EntityId, Confidence: destinations[0].Confidence},
		KeepUpright: intent.Constraints.KeepUpright,
	}, nil
}

func (c *Client) Execute(ctx context.Context, taskID string, step taskgraph.SkillStep) (agent.SkillResult, error) {
	parameters, err := structpb.NewStruct(step.Arguments)
	if err != nil {
		return agent.SkillResult{}, err
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
	lease := step.LeaseMS
	if lease == 0 {
		lease = 5_000
	}
	commandID, idempotencyKey := commandIdentity(taskID, step)
	stream, err := c.robot.ExecuteSkill(ctx, &robotv1.SkillCommand{
		SchemaVersion: "robot.v1", CommandId: commandID, TaskId: taskID, Skill: step.Skill,
		TargetRef: target, Parameters: parameters, DeadlineUnixMs: deadline, LeaseMs: lease,
		IdempotencyKey: idempotencyKey, SafetyProfile: c.profile, ApprovalId: step.ApprovalID,
	})
	if err != nil {
		return agent.SkillResult{}, err
	}
	var terminal *robotv1.SkillEvent
	for {
		event, recvErr := stream.Recv()
		if recvErr != nil {
			if terminal != nil {
				break
			}
			return agent.SkillResult{}, recvErr
		}
		if event.Type == robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED || event.Type == robotv1.SkillEventType_SKILL_EVENT_FAILED || event.Type == robotv1.SkillEventType_SKILL_EVENT_SAFETY_STOPPED || event.Type == robotv1.SkillEventType_SKILL_EVENT_CANCELLED {
			terminal = event
		}
	}
	return agent.SkillResult{
		Success: terminal.Type == robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED,
		Code:    terminal.Code, Message: terminal.Message, ObservationID: terminal.ObservationId,
		VerificationConfidence: terminal.VerificationConfidence,
	}, nil
}

func commandIdentity(taskID string, step taskgraph.SkillStep) (string, string) {
	idempotencyKey := step.IdempotencyKey
	if idempotencyKey == "" {
		idempotencyKey = taskID + ":read:" + step.ID
	}
	return taskID + ":" + step.ID, idempotencyKey
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
