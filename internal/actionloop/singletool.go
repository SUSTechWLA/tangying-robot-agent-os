package actionloop

import (
	"context"
	"fmt"
	"strings"
)

// SingleToolDecider calls the one tool it was offered, and refuses to do anything
// else.
//
// # Why this is not a hardcoded plan
//
// The rule this repository follows is that the model chooses which tools to call
// and in what order, rather than that choice being written into Go. This decider
// does not weaken that rule; it identifies the case where there is no choice to
// make. An action that declares exactly one tool has a choice set of size one, so
// a decider cannot add information by picking it — but it can add a failure mode
// by picking it wrongly, and it can add a dependency by requiring a model to be
// reachable before a read can happen at all.
//
// The declaration being executed still lives in data (the recovery catalog's
// action → tool list), not here. This type knows nothing about recovery, about
// which tool it is calling, or about what any tool means.
//
// # The two things it refuses
//
//   - **A tool that takes arguments.** There is no basis for inventing values for
//     them, and a plausible-looking invented value sent to a robot is worse than
//     not calling. Choosing arguments is exactly the part that needs a model (or
//     a person), so this decider declines instead of guessing.
//   - **A second call.** It calls the tool once. If the call failed, it stops and
//     says so rather than trying again: a fixed automatic retry is the thing this
//     system deliberately does not have, and a loop that retried would be one,
//     just written in a different file. Whether to try something else is a
//     decision for the caller, which has the whole picture.
//
// A multi-tool action must be driven by a real decider. If this type is handed
// more than one tool it blocks, so a wiring mistake that pointed a multi-tool
// action at it fails closed rather than picking the first tool arbitrarily.
type SingleToolDecider struct{}

// Decide implements Decider.
func (SingleToolDecider) Decide(_ context.Context, request Request) (Decision, error) {
	if len(request.Tools) != 1 {
		return Decision{Blocked: fmt.Sprintf(
			"这个动作声明了 %d 个工具，需要由模型决定用哪些、按什么顺序；"+
				"没有配置模型时不会替你选", len(request.Tools))}, nil
	}
	tool := request.Tools[0]
	if len(tool.Parameters) > 0 {
		return Decision{Blocked: fmt.Sprintf(
			"%s 需要参数（%s），参数值必须由模型或人给出，不能编造",
			tool.Name, strings.Join(tool.Parameters, "、"))}, nil
	}

	// The history is what makes this idempotent. The loop asks again after each
	// call, and the second question must not be answered with a second call.
	for _, round := range request.History {
		if round.Tool != tool.Name {
			continue
		}
		if round.Verdict == VerdictSatisfied {
			return Decision{
				Done:   true,
				Reason: fmt.Sprintf("唯一声明的工具 %s 已成功调用，且没有别的工具可选", tool.Name),
			}, nil
		}
		// Not satisfied: the call happened and did not achieve its effect. No
		// retry here — see the type comment.
		detail := round.Detail
		if detail == "" {
			detail = round.Code
		}
		return Decision{Blocked: fmt.Sprintf(
			"%s 调用后没有达到预期效果（%s）；不会自动重试", tool.Name, detail)}, nil
	}

	return Decision{
		Tool:   tool.Name,
		Reason: "该动作只声明了一个工具，没有可编排的余地",
	}, nil
}
