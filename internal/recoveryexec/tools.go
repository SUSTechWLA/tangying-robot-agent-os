package recoveryexec

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

// Building the registry from what the robot actually declares.
//
// # The problem this solves
//
// A robot service that reports success has done exactly that: reported. The
// closure contract says a call that changes the physical world is not complete
// until a *fresh observation taken after it* confirms it. Services carry no such
// observation — `ServiceResponse` has `ok`, `code`, `message` and a free-form
// result — so a mutating service call read literally is always an unknown outcome,
// and the recovery loop would stop after the first one.
//
// That reading is correct and useless. The fix is not to relax the gate but to
// satisfy it: after a call that mutates the world, take an observation and hand it
// to the gate. That is what the runner already does for task steps, and this is the
// same rule applied to the service surface rather than a second rule invented for
// it.
//
// # What decides the levels
//
// The robot declares `mutates_world` per service, and that is the only thing it
// declares about consequence. So it is what is used: a service that says it changes
// the world is treated as physical and goes through the scope check, and one that
// says it does not is treated as local. Nothing here guesses a level the robot did
// not state — a guess would be a second opinion about the robot's own hardware.

// ServiceCaller is the robot surface this adapter needs. It is narrowed to the two
// calls used so a test does not have to stand up a gRPC server.
type ServiceCaller interface {
	ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error)
	CallService(ctx context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error)
}

// EvidenceSource takes a fresh observation and renders it as closure evidence.
//
// It is called after a call that mutates the world, and its answer is what the
// gate judges. A source that fails leaves the evidence nil, which the gate reads
// as "not confirmed" — the safe direction: an unavailable observation must not be
// able to complete a physical write.
type EvidenceSource func(ctx context.Context) *closedloop.Evidence

// RobotServices builds a registry from the robot's declared service catalogue.
//
// Services the robot reports as unavailable are left out. That is deliberate: an
// unavailable service offered as a tool is a choice the model can make and that
// cannot succeed, and the rounds spent discovering that are the operator's time.
// The catalogue is re-read on each call, so a service that becomes available is
// immediately usable.
func RobotServices(caller ServiceCaller, evidence EvidenceSource) Registry {
	return &serviceRegistry{caller: caller, evidence: evidence}
}

type serviceRegistry struct {
	caller   ServiceCaller
	evidence EvidenceSource
}

// Lookup implements Registry by reading the robot's catalogue.
func (r *serviceRegistry) Lookup(name string) (Tool, bool) {
	catalogue, err := r.caller.ListServices(context.Background())
	if err != nil || catalogue == nil {
		return Tool{}, false
	}
	for _, service := range catalogue.Services {
		if service.GetName() != name || !service.GetAvailable() {
			continue
		}
		return Tool{
			Name:         name,
			Description:  service.GetDescription(),
			Parameters:   parametersOf(service),
			SafetyLevel:  levelOf(service),
			MutatesWorld: service.GetMutatesWorld(),
			Call:         r.callFor(name, service.GetMutatesWorld()),
		}, true
	}
	return Tool{}, false
}

func (r *serviceRegistry) callFor(name string, mutates bool) func(context.Context, map[string]any) (actionloop.Result, error) {
	return func(ctx context.Context, arguments map[string]any) (actionloop.Result, error) {
		parameters, err := structFrom(arguments)
		if err != nil {
			return actionloop.Result{}, err
		}
		response, err := r.caller.CallService(ctx, &robotv1.ServiceRequest{
			Name: name, RequestId: requestID(name), Parameters: parameters,
		})
		if err != nil {
			return actionloop.Result{
				Success: false, Code: transportCode(err, mutates),
				Message: fmt.Sprintf("调用 %s 没有得到应答：%v", name, err),
			}, nil
		}
		result := actionloop.Result{
			Success: response.GetOk(), Code: response.GetCode(),
			Message: response.GetMessage(),
		}
		if response.GetResult() != nil {
			result.Detail = response.GetResult().AsMap()
		}
		if !response.GetOk() {
			return result, nil
		}
		// Success. If it changed the world, the gate needs an observation taken
		// after the call — the service does not carry one.
		if mutates && r.evidence != nil {
			result.Evidence = r.evidence(ctx)
		}
		return result, nil
	}
}

// levelOf derives the safety level from the robot's own declaration.
func levelOf(service *robotv1.ServiceDefinition) skills.SafetyLevel {
	if service.GetMutatesWorld() {
		return skills.SafetyPhysical
	}
	return skills.SafetyLocal
}

// parametersOf reads the declared parameter names out of the service's schema.
func parametersOf(service *robotv1.ServiceDefinition) []string {
	schema := service.GetInputSchema()
	if schema == nil {
		return nil
	}
	properties, ok := schema.AsMap()["properties"].(map[string]any)
	if !ok {
		return nil
	}
	names := make([]string, 0, len(properties))
	for name := range properties {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// structFrom renders the decider's arguments as the protobuf struct the service
// request carries.
//
// The model's arguments are free-form, so a value it cannot express is reported
// rather than silently dropped: a call made with half its arguments is a call that
// fails at the robot for a reason nobody can see.
func structFrom(arguments map[string]any) (*structpb.Struct, error) {
	if len(arguments) == 0 {
		return nil, nil
	}
	parameters, err := structpb.NewStruct(arguments)
	if err != nil {
		return nil, fmt.Errorf("参数无法转换成服务请求：%w", err)
	}
	return parameters, nil
}

// transportCode decides what a failed call means, using something the classifier
// cannot know: whether this call changes the world.
//
// The classification table maps the transport codes to TRANSIENT — "the connection
// is down; once it is back, repeating the command is the right move". That is true
// of a call that never left, and it is not true of one that was in flight when the
// answer stopped coming: the robot may have acted, and "retry" is then the one
// instruction the closed-loop contract forbids.
//
// A code-to-class table has only the code to go on, so it cannot tell those apart.
// This function can, because it knows whether the tool mutates anything. It is not
// a second classification: it chooses between two codes the table already defines,
// and the choice is made where the missing fact is available.
func transportCode(err error, mutates bool) string {
	switch status.Code(err) {
	case codes.DeadlineExceeded, codes.Canceled, codes.Internal, codes.Unknown, codes.DataLoss:
		// The request may have been delivered. Whether the robot acted is not
		// established, and only a mutating call makes that matter — a read that
		// timed out changed nothing.
		if mutates {
			return "EXECUTION_OUTCOME_UNKNOWN"
		}
		return "RPC_DEADLINE_EXCEEDED"
	default:
		// Unavailable, unauthenticated, permission denied: the request never
		// reached the robot, so nothing happened and repeating is safe.
		return "RPC_UNAVAILABLE"
	}
}

func requestID(name string) string {
	return fmt.Sprintf("recovery-%s-%d", strings.ReplaceAll(name, ".", "-"), time.Now().UnixNano())
}

// LocalTools are the operations this agent performs itself, by the names the
// recovery catalogue refers to them by.
//
// They are separate from the robot's services because they are not the robot's:
// reading the execution ledger and resuming a task happen here. Listing them
// explicitly is the point — the recovery catalogue names them, so a deployment
// that cannot perform one says so instead of resolving it to nothing.
type LocalTools struct {
	// ReadTelemetry is `telemetry.read`.
	ReadTelemetry func(ctx context.Context) (actionloop.Result, error)
	// ReadHistory is `execution.read-history`.
	ReadHistory func(ctx context.Context, arguments map[string]any) (actionloop.Result, error)
	// Reconnect is `runtime.reconnect`.
	Reconnect func(ctx context.Context) (actionloop.Result, error)
	// ResumeTask is `task.resume`.
	ResumeTask func(ctx context.Context, arguments map[string]any) (actionloop.Result, error)
	// SafePose is `recover_to_safe_pose`, which is a robot skill reached through
	// the task surface rather than a service.
	SafePose func(ctx context.Context, arguments map[string]any) (actionloop.Result, error)
}

// Registry renders the local operations as tools. Operations left nil are simply
// absent, so an action that needs one reports the missing tool by name.
func (l LocalTools) Registry() Registry {
	registry := MapRegistry{}
	add := func(name, description string, level skills.SafetyLevel, mutates bool, call func(context.Context, map[string]any) (actionloop.Result, error)) {
		// The nil check is on the *underlying* function, and the wrappers below are
		// only built when it is present. Wrapping first and checking the wrapper
		// would defeat the check: a closure over a nil field is not nil, so an
		// operation nobody wired would be offered as a tool and fail at the call.
		if call == nil {
			return
		}
		registry[name] = Tool{
			Name: name, Description: description, SafetyLevel: level, MutatesWorld: mutates,
			Call: call,
		}
	}
	noArguments := func(inner func(context.Context) (actionloop.Result, error)) func(context.Context, map[string]any) (actionloop.Result, error) {
		if inner == nil {
			return nil
		}
		return func(ctx context.Context, _ map[string]any) (actionloop.Result, error) { return inner(ctx) }
	}
	add("telemetry.read", "读取最新的机器人观测与遥测", skills.SafetyReadOnly, false, noArguments(l.ReadTelemetry))
	add("execution.read-history", "读取该任务的执行记录", skills.SafetyReadOnly, false, l.ReadHistory)
	add("runtime.reconnect", "重连机器人运行时", skills.SafetyLocal, false, noArguments(l.Reconnect))
	add("task.resume", "让任务从安全点继续", skills.SafetyPhysical, false, l.ResumeTask)
	add("recover_to_safe_pose", "机械臂回到安全姿态", skills.SafetyPhysical, true, l.SafePose)
	return registry
}

// Combined resolves a name against several registries in order.
//
// It exists so a deployment can put the robot's services and this agent's own
// operations in one namespace without either having to know about the other. The
// first registry that has the name wins, and a name in neither is reported missing
// by the executor with the name intact.
func Combined(registries ...Registry) Registry {
	return combinedRegistry(registries)
}

type combinedRegistry []Registry

func (c combinedRegistry) Lookup(name string) (Tool, bool) {
	for _, registry := range c {
		if registry == nil {
			continue
		}
		if tool, ok := registry.Lookup(name); ok {
			return tool, true
		}
	}
	return Tool{}, false
}

// EvidenceFromSnapshot renders one telemetry snapshot as closure evidence.
//
// It is the same construction the task runner uses, kept here because the service
// surface needs it too. The freshness rules are the ones that matter: a snapshot
// taken while the robot is in an emergency stop cannot confirm a physical write,
// because the tool physically did nothing.
func EvidenceFromSnapshot(snapshot telemetry.Snapshot, fallbackID string, now time.Time) *closedloop.Evidence {
	evidence := &closedloop.Evidence{
		ObservationID: fallbackID,
		ObservedAt:    snapshot.ObservedAt.UTC(),
		SourceID:      snapshot.RobotID,
	}
	if snapshot.Reconstruction != nil {
		if snapshot.Reconstruction.ObservationID != "" {
			evidence.ObservationID = snapshot.Reconstruction.ObservationID
		}
		if snapshot.Reconstruction.ObservedAtUnixMS > 0 {
			evidence.ObservedAt = time.UnixMilli(snapshot.Reconstruction.ObservedAtUnixMS).UTC()
		}
		evidence.SourceID = snapshot.Reconstruction.SourceID
	}
	// Computed from the source's declared budget, not assumed. The literal
	// "FRESH" that used to be here meant the staleness rule could only ever fire
	// on an emergency stop.
	evidence.Freshness = string(snapshot.EvidenceFreshness(now))
	if snapshot.EmergencyStopped {
		evidence.Freshness = "UNKNOWN"
	}
	for _, anomaly := range snapshot.Anomalies {
		if anomaly == "EMERGENCY_STOP_LATCHED" {
			evidence.Freshness = "UNKNOWN"
		}
	}
	return evidence
}

// ErrNoServices means the robot reported no service catalogue at all.
var ErrNoServices = errors.New("the robot reported no service catalogue")
