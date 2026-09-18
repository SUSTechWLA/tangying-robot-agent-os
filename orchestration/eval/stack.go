package eval

import (
	"errors"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// Stack scores a real parser and planner pair in process.
//
// It is constructed from the same configuration the Local Agent reads, so
// "score the deployed stack" and "score a candidate endpoint" are the same
// operation with one field changed. That is the property a post-training loop
// needs: a checkpoint is accepted or rejected by pointing this at its server and
// comparing two Reports, not by reading samples.
type Stack struct {
	Label   string
	Parser  intent.Parser
	Planner orchestration.Planner
	// Catalog is the run's skill catalog, kept so a report can say which skills
	// were even available when a plan was judged.
	Catalog []skills.SkillManifest
	// World is the runtime state the plan is scored against. It defaults to the
	// unknown world, so a case that does not care about navigation is not silently
	// scored against a deployment's particular room layout.
	World orchestration.World
}

// Name implements System.
func (s Stack) Name() string {
	if s.Label != "" {
		return s.Label
	}
	return "stack"
}

// Answer implements System by running the same two steps the task service runs:
// parse the request into an intent, then plan that intent into a skill graph.
//
// A refusal at either step is reported as a refusal rather than an error, because
// declining a request is a correct outcome for some requests and the score has to
// be able to reward it.
func (s Stack) Answer(request string) (Answer, error) {
	parsed, err := s.Parser.Parse(request)
	if err != nil {
		if isRefusal(err) {
			return Answer{Refused: true, RefusalReason: err.Error()}, nil
		}
		return Answer{}, err
	}
	bundle, err := s.Planner.Plan(request, parsed, s.World)
	if err != nil {
		// The planner refusing is also a refusal: it looked at the intent and
		// declined to build a graph from it.
		return Answer{
			Intent: parsed, Refused: true,
			RefusalReason: "planner declined: " + err.Error(),
		}, nil
	}
	if len(bundle.Plans) == 0 && len(bundle.Rejections) > 0 {
		return Answer{
			Intent: parsed, Bundle: &bundle, Refused: true,
			RefusalReason: strings.Join(bundle.Rejections, "; "),
		}, nil
	}
	return Answer{Intent: parsed, Bundle: &bundle}, nil
}

// isRefusal reports whether an error means "this request will not be planned"
// rather than "something broke".
//
// The distinction is the whole point of scoring refusals separately: a model that
// cannot be reached must not be recorded as a model that correctly declined.
func isRefusal(err error) bool {
	if errors.Is(err, intent.ErrClarificationRequired) {
		return true
	}
	// The parser reports an unsupported request as a clarification too, but a
	// deployment may wrap it; match on the sentinel text as a fallback so a wrap
	// does not turn a correct refusal into a run failure.
	message := err.Error()
	return strings.Contains(message, "unsupported intent") ||
		strings.Contains(message, "clarification required") ||
		strings.Contains(message, "需要明确") ||
		strings.Contains(message, "尚未支持")
}

// NewStack builds the system under test from a planner configuration.
//
// It mirrors the composition root deliberately: an evaluation that assembled its
// own parser and planner could pass while the deployed wiring fails, which is the
// failure mode this repository has already been bitten by once (see the muted
// observer incident). The cost is that this function must be kept in step with
// cmd/local-agent; the benefit is that a green run says something about the
// product rather than about the harness.
func NewStack(label string, config agent.Config, plannerConfig orchestration.Config) Stack {
	return Stack{
		Label:   label,
		Parser:  agent.NewParser(config),
		Planner: orchestration.New(manipulation.Catalog(), plannerConfig),
		Catalog: manipulation.Catalog(),
	}
}
