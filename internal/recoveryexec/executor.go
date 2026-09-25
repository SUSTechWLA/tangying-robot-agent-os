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
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
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
	// DeciderProvider resolves the currently configured model for each recovery
	// attempt. Nil falls back to Decider and the existing safe no-model behavior.
	DeciderProvider func() actionloop.Decider
	Registry        Registry
	Observer        Observer
	Verify          Verify
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
	// Record is told about every attempt, successful or not.
	//
	// It is a port rather than a ledger dependency so this package stays free of
	// storage, and it is called on every path — a refusal before anything ran is
	// part of the task's story too, and a replay that showed only the executions
	// would make "we approved this and it was refused" indistinguishable from
	// "nobody ever asked".
	//
	// Nil means no ledger here. That is a real state (a test, a bare deployment)
	// and it is not an error: an unrecorded execution still happened.
	Record func(ctx context.Context, request Request, result Result, err error)
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
	// Executed is true when at least one of the action's tools was actually
	// dispatched — not merely when the loop returned.
	//
	// The distinction is not pedantic: a loop that could not pick a tool because
	// no decider was configured returns without an error, and reporting that as
	// "executed" tells an operator a physical action happened when nothing was
	// called. The count comes from the loop because only the loop knows.
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

// Execute runs one approved action and records the attempt.
//
// The recording is done by the wrapper rather than at each return, because
// Execute has six ways to end and a seventh added later would silently skip the
// record. Here the record is written on every path by construction — including
// the error paths, since "we tried and the attempt itself broke" is part of the
// task's story as much as a clean refusal is.
func (e *Executor) Execute(ctx context.Context, request Request) (Result, error) {
	result, err := e.execute(ctx, request)
	e.record(ctx, request, result, err)
	return result, err
}

// record hands one attempt to the ledger port, when one is configured.
//
// A failure here is logged and swallowed: recording must never decide whether an
// action happened, and by this point it already has.
func (e *Executor) record(ctx context.Context, request Request, result Result, err error) {
	if e.Record == nil {
		return
	}
	e.Record(ctx, request, result, err)
}

// execute runs one approved action.
//
// The order of the checks is the safety argument, so it is stated rather than
// implied: the action must be executable at all, then its declared tools must
// exist here, then the risk decides whether a person is asked, and only then does
// anything run. Every stage that ends the attempt records why.
func (e *Executor) execute(ctx context.Context, request Request) (Result, error) {
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
	tools, missing := e.resolve(request.Action)
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
	//
	// "Asked" means asked of somebody who is not the initiator. When a person
	// started this execution themselves — they pressed the button that runs this
	// action — the click *is* the approval, and routing it through a port that
	// answers "yes, because you asked" would be ceremony that hides who decided.
	// The port is for every other initiator: a future automatic path, a timer, an
	// escalation rule. Those have nobody at the keyboard, so they must ask.
	if request.Action.RequiresApproval() {
		switch {
		case request.OperatorApproved:
			// A person started this. The record says so explicitly: "who approved
			// this" has to be answerable from the trail, and a step that merely
			// says a check passed would not answer it.
			trail("approval.operator", "操作者本人发起了这次执行，点击即批准")
		case e.Approve == nil:
			result.Reason = fmt.Sprintf("%s 需要人工批准，但这个部署没有配置批准入口", request.Action.ID)
			trail("approval.unavailable", result.Reason)
			return result, nil
		default:
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
			trail("approval.granted", "批准端口同意执行")
		}
	}

	// 4. Run it. The scope is the declared tools and nothing else: the approval was
	// given for this action, not for whatever the decider thinks would help.
	loop := actionloop.Loop{
		Tools:   tools,
		Decider: e.decider(tools, request.Action),
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
	// The task is stated by the runtime, so a tool that needs it does not have to
	// be told by a decider. See context.go for why that distinction matters.
	outcome, err := loop.Run(WithTaskID(ctx, request.TaskID), request.Action.Summary)
	if err != nil {
		return result, err
	}
	result.Executed = outcome.Calls > 0
	result.Rounds = outcome.Rounds
	result.Reason = outcome.Reason
	if result.Executed {
		trail("executed", outcome.Reason)
	} else {
		// Nothing left the process, and the trail says exactly that rather than
		// leaving a gap a reader would have to interpret.
		trail("not-executed", outcome.Reason)
	}

	// 5. The verdict comes from looking, not from the action's own account.
	//
	// Nothing was called, so there is nothing to look for. Verifying anyway would
	// invite a verifier to report on a state this action never touched, and a
	// "verified" answer for an action that did not run is the worst possible
	// output of this package.
	if !result.Executed {
		result.Verification = "没有执行任何动作，因此没有可复验的结果"
		trail("verification.not-applicable", result.Verification)
		return result, nil
	}
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

// Request is one step to execute.
type Request struct {
	Action agentruntime.RecoveryAction
	PlanID string
	TaskID string
	// OperatorApproved says a person started this execution themselves.
	//
	// It is set by the surface a person is using, and only there. It answers the
	// approval question directly instead of asking a port to relay it, so the
	// record shows that a person decided rather than that a function returned true.
	//
	// A caller that sets this without a person behind it is claiming an approval
	// that did not happen. It exists because the alternative — an approver that
	// always says yes — would make "who approved this" unanswerable from the code.
	OperatorApproved bool
	// ApprovalEvidence names the surface the approval came from, in a form a
	// reader can check: which console session, from where, at what time.
	//
	// OperatorApproved is a boolean, and a boolean is exactly what an operator
	// cannot audit. This carries the part that can be disagreed with. It is
	// optional because a caller without a session to name — a test, a CLI —
	// should say so by leaving it empty rather than by inventing one.
	ApprovalEvidence string
}

// resolve turns an action's declared tool names into callable tools.
//
// # Why the robot's own word is not the whole answer
//
// The registry builds each tool from the robot's service catalogue, so
// MutatesWorld arrives from the robot: it says which of its services change the
// world. That statement is the only one the robot makes about consequence, and
// it is the right source for a safety level.
//
// It is the wrong *sole* source for a gate. The gate decides whether a returned
// success may be recorded as done, so a robot reporting `mutates_world=false` for
// a service that moves an arm is not describing itself — it is switching its own
// closure check off, and the governed party does not get to exempt itself.
//
// Two trusted local declarations supply the floor, combined the way
// edge/agent/runner.go already combines its catalogue with a connected runtime's:
// OR them, so the far end can add a gate and never remove one.
//
//   - The action's own MovesTools, declared in the recovery catalogue next to its
//     risk and its tools. This is what covers the recovery surface, whose service
//     names (`mapping.move`, `recover_to_safe_pose`) are not the task skill names,
//     and it is per tool because `mapping.start` opens a survey session while
//     `mapping.move` drives the base.
//   - core/robotcontract.PhysicalTool, the profile-validation list of tools that
//     move the machine whatever any adapter says.
func (e *Executor) resolve(action agentruntime.RecoveryAction) ([]actionloop.Tool, []string) {
	moves := make(map[string]bool, len(action.MovesTools))
	for _, name := range action.MovesTools {
		moves[name] = true
	}
	declared := action.Tools
	tools := make([]actionloop.Tool, 0, len(declared))
	missing := make([]string, 0)
	for _, name := range declared {
		tool, ok := e.Registry.Lookup(name)
		if !ok {
			missing = append(missing, name)
			continue
		}
		trustedMutation := moves[name] || robotcontract.PhysicalTool(name)
		tools = append(tools, actionloop.Tool{
			Name: tool.Name, Description: tool.Description, Parameters: tool.Parameters,
			SafetyLevel:  tool.SafetyLevel,
			MutatesWorld: trustedMutation || tool.MutatesWorld,
			Call:         tool.Call,
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

// decider picks how this attempt's tool choice is made.
//
// A configured model always wins: when one exists it decides, including for a
// single-tool action, because it can also decide the arguments and can notice
// that no call is warranted at all.
//
// With no model, a single-tool action still has a way through, and where that is
// allowed is deliberately narrow:
//
//   - Only when the action is read-only. A single-tool *mutating* action with no
//     model stays blocked: what a model contributes there is not the choice of
//     tool (there is only one) but the judgement that calling it is right, and an
//     unconfigured deployment has nobody making that call. In practice this
//     changes nothing for the operator path, since a bounded write requires
//     approval and so never reaches here without a person.
//   - Only when the tool takes no arguments, which actionloop.SingleToolDecider
//     enforces by refusing rather than inventing values.
//
// The effect is that looking at the robot keeps working on a deployment with no
// model, while changing it does not. That asymmetry is intended: a system that
// can notice a problem but never investigate it is not safer, only louder.
func (e *Executor) decider(tools []actionloop.Tool, action agentruntime.RecoveryAction) actionloop.Decider {
	if e.DeciderProvider != nil {
		if current := e.DeciderProvider(); current != nil {
			return current
		}
	}
	if e.Decider != nil {
		return e.Decider
	}
	if len(tools) == 1 && !toolListMutates(tools) && !action.RequiresApproval() {
		return actionloop.SingleToolDecider{}
	}
	return refusingDecider{}
}

// toolListMutates reports whether any declared tool can change the world.
//
// It is asked of the tools rather than read off the action's risk level, because
// the risk level is an editorial judgement made in the catalog while MutatesWorld
// is a declaration by the service itself. When the two disagree, the safety
// question should be answered by the service.
func toolListMutates(tools []actionloop.Tool) bool {
	for _, tool := range tools {
		if tool.MutatesWorld {
			return true
		}
	}
	return false
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
