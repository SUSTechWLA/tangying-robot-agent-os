// Package recoveryexec executes a recovery action an operator approved.
//
// # The gap it fills
//
// The recovery agent investigates, proposes and stops. Its plans are published as
// `ops.recovery_plan` and displayed in the console, and until now nothing
// consumed them: an operator read "load a saved map" and did it by hand. This
// package is the missing half — approve a step and it runs.
//
// # No hardcoded orchestration
//
// The tempting implementation is a `switch` over the thirteen catalog ids, each
// case calling the one service that action maps to. That is a hardcoded
// orchestration table, and it would put the knowledge of "what does map.activate
// actually do" in the executor instead of next to the action, where the operator
// reviewing the catalog can see it.
//
// So the catalog declares `Tools` — the concrete tools each action may call — and
// this package runs a decision loop over exactly those. The model chooses which
// of the declared tools to call and in what order, given the current observation;
// the declaration decides what it is even allowed to consider.
//
// That declaration does double duty: it is the **scope** the approval was given
// against. An operator approved "load a saved map", not "whatever the model
// decides would help", so a call outside the declared tools is refused and the
// loop stops. This is the same rule the execution loop applies to a plan's tools,
// reused rather than re-argued.
//
// # What it does not do
//
// It does not decide whether to recover, and it does not verify that recovery
// worked by asking the action how it went. The verdict comes from re-reading the
// facts: `Verify` is required, and an outcome with no verification is reported as
// unverified rather than as success.
package recoveryexec

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

// Tool is one callable thing, in the shape the loop needs.
//
// It carries the manifest vocabulary rather than a private one: the safety level
// is what decides whether the call needs approval, and keeping a second notion of
// "how dangerous is this" here is how the two come to disagree.
type Tool struct {
	Name         string
	Description  string
	Parameters   []string
	SafetyLevel  skills.SafetyLevel
	MutatesWorld bool
	Call         func(ctx context.Context, arguments map[string]any) (actionloop.Result, error)
}

// Registry is what a deployment can actually call.
//
// It is asked at execution time rather than captured at construction, because the
// robot's services are registered by the robot: a service that appeared after this
// process started is one the action can use, and a service that went away is one
// it cannot. Resolving late turns "the map service is not registered on this
// robot" into a sentence instead of a call that fails at the far end.
type Registry interface {
	// Lookup returns the tool by name, or false when the deployment has no such
	// tool.
	Lookup(name string) (Tool, bool)
}

// Observer reads the facts the loop decides from and verifies against.
type Observer interface {
	// Observe returns the current state, in the shape the loop shows the model.
	Observe(ctx context.Context, taskID string) (actionloop.Observation, error)
}

// Verify reports whether the action's intended effect can now be seen.
//
// It is a port rather than a claim the action makes about itself. A recovery
// action that says it worked while the fault is still there is the exact thing
// this package exists to avoid recording as success.
type Verify func(ctx context.Context, action agentruntime.RecoveryAction, outcome actionloop.Outcome) (Verdict, error)

// Verdict is the answer to "did it actually work".
type Verdict struct {
	// Verified is true only when the effect was observed.
	Verified bool
	// Detail is what was observed, in the operator's words.
	Detail string
}

// Executor runs approved actions.
type Executor struct {
	Registry Registry
	Observer Observer
	Verify   Verify
	// Decider chooses among the action's declared tools. Nil means a default that
	// refuses every call, which is the safe reading of "nobody can decide" — the
	// action then ends unexecuted with that reason rather than pretending.
	Decider actionloop.Decider
	// Approve is consulted for an action whose risk requires it. It is passed in
	// rather than read from a flag so that "who may approve" stays decided once,
	// where the rest of the system decides it.
	Approve func(ctx context.Context, request ApprovalRequest) (bool, error)
	// MaxRounds caps the decisions one action may take. Zero means the loop default.
	MaxRounds int
	// Now is the clock. Tests set it.
	Now func() time.Time
}

// ApprovalRequest is what a person is asked.
type ApprovalRequest struct {
	Action  agentruntime.RecoveryAction
	PlanID  string
	TaskID  string
	Summary string
	Tools   []string
}

// Result is what one attempt produced.
type Result struct {
	ActionID string `json:"actionId"`
	// Executed is true when the loop ran the action's tools and stopped.
	Executed bool `json:"executed"`
	// Verified is true only when Verify saw the effect.
	Verified bool `json:"verified"`
	// Reason is a sentence for the operator.
	Reason string `json:"reason"`
	// Rounds is the decision record, so a reader can see what was tried and why,
	// not only what the outcome was.
	Rounds []actionloop.Round `json:"rounds,omitempty"`
	// Verification is what the post-check saw.
	Verification string `json:"verification,omitempty"`
	// Trail records the tool resolution, so a plan that could not run says which
	// tool was missing rather than only that it failed.
	Trail []Step `json:"trail,omitempty"`
}

// Step is one recorded stage of the attempt.
type Step struct {
	Name   string    `json:"name"`
	Detail string    `json:"detail,omitempty"`
	At     time.Time `json:"at"`
}

// Errors callers act on.
var (
	// ErrNotExecutable means this action has no runnable form in this deployment.
	ErrNotExecutable = errors.New("this action is not executable here")
)

// Execute runs one approved action.
//
// The order of the checks is the safety argument, so it is stated rather than
// implied: the action must be executable at all, then its declared tools must
// exist here, then the risk decides whether a person is asked, and only then does
// anything run. Every stage that ends the attempt records why.
func (e *Executor) Execute(ctx context.Context, request Request) (Result, error) {
	result := Result{ActionID: request.Action.ID}
	trail := func(name, detail string) {
		result.Trail = append(result.Trail, Step{Name: name, Detail: detail, At: e.now()})
	}

	// 1. The catalog decides whether this action may run at all.
	if !request.Action.Executable() {
		result.Reason = request.Action.Refusal
		if result.Reason == "" {
			result.Reason = "这个动作不允许由系统执行"
		}
		trail("catalog.refused", result.Reason)
		return result, nil
	}
	if len(request.Action.Tools) == 0 {
		// A catalogue entry that names no tools is a wiring gap, and executing it
		// would mean inventing a mapping — which is the thing this package refuses
		// to do.
		result.Reason = fmt.Sprintf("%s 在目录里没有声明可调用的工具，无法执行", request.Action.ID)
		trail("catalog.no-tools", result.Reason)
		return result, fmt.Errorf("%w: %s", ErrNotExecutable, request.Action.ID)
	}

	// 2. The declared tools have to exist here.
	tools, missing := e.resolve(request.Action.Tools)
	sort.Strings(missing)
	if len(missing) > 0 {
		result.Reason = fmt.Sprintf(
			"这台机器人没有提供 %s，因此 %s 无法执行；不需要重试，需要先把对应的能力接上",
			strings.Join(missing, "、"), request.Action.ID)
		trail("tools.missing", strings.Join(missing, ","))
		return result, nil
	}
	trail("tools.resolved", strings.Join(names(tools), ","))

	// 3. The risk decides whether a person is asked. Read-only does not ask; a
	// bounded write does; a never-automatic action never reaches here.
	if request.Action.RequiresApproval() {
		if e.Approve == nil {
			result.Reason = fmt.Sprintf("%s 需要人工批准，但这个部署没有配置批准入口", request.Action.ID)
			trail("approval.unavailable", result.Reason)
			return result, nil
		}
		approved, err := e.Approve(ctx, ApprovalRequest{
			Action: request.Action, PlanID: request.PlanID, TaskID: request.TaskID,
			Summary: request.Action.Summary, Tools: names(tools),
		})
		if err != nil {
			return result, fmt.Errorf("ask for approval: %w", err)
		}
		if !approved {
			result.Reason = fmt.Sprintf("%s 没有得到批准", request.Action.ID)
			trail("approval.refused", result.Reason)
			return result, nil
		}
		trail("approval.granted", "operator approved")
	}

	// 4. Run it. The scope is the declared tools and nothing else: the approval was
	// given for this action, not for whatever the decider thinks would help.
	loop := actionloop.Loop{
		Tools:   tools,
		Decider: e.decider(),
		Observe: func(ctx context.Context) (actionloop.Observation, error) { return e.observe(ctx, request.TaskID) },
		// A second line of defence, not the first.
		//
		// The first is that `tools` above contains only the action's declared
		// names, so the decider is never offered anything else — it cannot choose
		// what it cannot see, which is better than refusing after the fact.
		//
		// This bound is kept anyway because the two protect different changes. The
		// tool list is what this function happens to pass; the scope is what was
		// approved. If `resolve` is ever widened — "let the model see everything the
		// robot offers" is a plausible edit — the list stops bounding anything and
		// this is what still holds. Removing it would make the approval's meaning
		// depend on how a list happens to be built.
		Scope:     actionloop.ScopeOf(request.Action.Tools...),
		MaxRounds: e.MaxRounds,
		Now:       e.Now,
		// The operator has just approved this specific action, so the loop's own
		// approval question is already answered. It is answered here rather than
		// skipped: the loop still asks, and this is the answer.
		Approve: func(context.Context, actionloop.Tool, map[string]any) (bool, error) { return true, nil },
	}
	outcome, err := loop.Run(ctx, request.Action.Summary)
	if err != nil {
		return result, err
	}
	result.Executed = true
	result.Rounds = outcome.Rounds
	result.Reason = outcome.Reason
	trail("executed", outcome.Reason)

	// 5. The verdict comes from looking, not from the action's own account.
	if e.Verify == nil {
		result.Verification = "没有配置复验，无法确认恢复是否生效"
		trail("verification.unavailable", result.Verification)
		return result, nil
	}
	verdict, err := e.Verify(ctx, request.Action, outcome)
	if err != nil {
		return result, fmt.Errorf("verify the action: %w", err)
	}
	result.Verified = verdict.Verified
	result.Verification = verdict.Detail
	trail("verified", verdict.Detail)
	return result, nil
}

// Request is one approved step.
type Request struct {
	Action agentruntime.RecoveryAction
	PlanID string
	TaskID string
}

func (e *Executor) resolve(declared []string) ([]actionloop.Tool, []string) {
	tools := make([]actionloop.Tool, 0, len(declared))
	missing := make([]string, 0)
	for _, name := range declared {
		tool, ok := e.Registry.Lookup(name)
		if !ok {
			missing = append(missing, name)
			continue
		}
		tools = append(tools, actionloop.Tool{
			Name: tool.Name, Description: tool.Description, Parameters: tool.Parameters,
			SafetyLevel: tool.SafetyLevel, MutatesWorld: tool.MutatesWorld, Call: tool.Call,
		})
	}
	return tools, missing
}

// refusingDecider is the default when none is configured.
//
// It refuses rather than fails, so the attempt ends with a sentence an operator
// can act on instead of an error about a nil interface.
type refusingDecider struct{}

func (refusingDecider) Decide(context.Context, actionloop.Request) (actionloop.Decision, error) {
	return actionloop.Decision{Blocked: "没有配置决策器，无法选择恢复动作"}, nil
}

func (e *Executor) decider() actionloop.Decider {
	if e.Decider != nil {
		return e.Decider
	}
	return refusingDecider{}
}

func (e *Executor) observe(ctx context.Context, taskID string) (actionloop.Observation, error) {
	if e.Observer == nil {
		return actionloop.Observation{Summary: "没有配置观测来源"}, nil
	}
	return e.Observer.Observe(ctx, taskID)
}

func (e *Executor) now() time.Time {
	if e.Now != nil {
		return e.Now().UTC()
	}
	return time.Now().UTC()
}

func names(tools []actionloop.Tool) []string {
	list := make([]string, 0, len(tools))
	for _, tool := range tools {
		list = append(list, tool.Name)
	}
	sort.Strings(list)
	return list
}

// MapRegistry is a Registry over a fixed set of tools.
//
// It is what a deployment builds once from the robot's service catalogue plus the
// operations this agent performs itself, and what tests use directly.
type MapRegistry map[string]Tool

// Lookup implements Registry.
func (r MapRegistry) Lookup(name string) (Tool, bool) {
	tool, ok := r[name]
	return tool, ok
}
