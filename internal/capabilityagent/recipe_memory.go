package capabilityagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
	"google.golang.org/protobuf/types/known/structpb"
)

const recipeVersion = "planning-recipe.v1"
const recipeLimit = 32
const recipeMaxBytes = 64 * 1024
const recipeTTL = 24 * time.Hour

// RecipeMemory is an optional, bounded accelerator for validated PROPOSALS.
// A recipe is not a successful execution, approval, command or observation.
// Mode hint always consults the model; validated may reuse an exact proposal
// after replaying all its planning reads and applying today's validators.
type RecipeMemory struct {
	Store     eventstore.Store
	Namespace string
	Mode      string
	mu        sync.Mutex
	now       func() time.Time
}

func NewRecipeMemory(store eventstore.Store, namespace, mode string) (*RecipeMemory, error) {
	if mode == "" || mode == "off" {
		return nil, nil
	}
	if err := ValidateRecipeMode(mode); err != nil {
		return nil, err
	}
	if store == nil || strings.TrimSpace(namespace) == "" {
		return nil, errors.New("recipe memory requires a durable store and model namespace")
	}
	return &RecipeMemory{Store: store, Namespace: namespace, Mode: mode, now: time.Now}, nil
}

func ValidateRecipeMode(mode string) error {
	if mode != "" && mode != "off" && mode != "hint" && mode != "validated" {
		return errors.New("AGENT_GOAL_RECIPE_MODE must be off, hint or validated")
	}
	return nil
}

// RecipeNamespace partitions model/endpoint/template changes without storing credentials.
func RecipeNamespace(endpoint modelroute.Endpoint) string {
	return capability.Fingerprint([]string{endpoint.Provider, strings.TrimRight(endpoint.BaseURL, "/"), endpoint.Model, endpoint.Cache.Namespace, actionloop.PromptTemplateVersion})
}

type planningRecipe struct {
	ID        string                    `json:"id"`
	Scope     string                    `json:"scope"`
	Goal      string                    `json:"goal"`
	CreatedAt time.Time                 `json:"createdAt"`
	Calls     []capability.Call         `json:"calls"`
	Reads     []capability.PlanningStep `json:"reads"`
}
type recipeBook struct {
	Version string           `json:"version"`
	Recipes []planningRecipe `json:"recipes"`
}

func (m *RecipeMemory) key(robot string) string {
	return "planning-recipes/" + capability.Fingerprint([]string{recipeVersion, m.Namespace, robot})
}
func (p *Planner) recipeScope(robot string, entries map[string]capability.Manifest) string {
	return capability.Fingerprint([]any{recipeVersion, p.Memory.Namespace, robot, capability.Fingerprint(entries), p.Grants, p.Progress})
}
func recipeID(r planningRecipe) string {
	return capability.Fingerprint([]any{recipeVersion, r.Scope, r.Goal, r.CreatedAt, r.Calls, r.Reads})
}
func (m *RecipeMemory) load(ctx context.Context, robot string) (eventstore.AggregateState, recipeBook, error) {
	s, found, err := m.Store.LoadState(ctx, m.key(robot))
	b := recipeBook{Version: recipeVersion}
	if err != nil || !found {
		return s, b, err
	}
	if s.AggregateID != m.key(robot) || s.Version == 0 {
		return s, b, errors.New("recipe state identity or version mismatch")
	}
	if len(s.Data) > recipeLimit*recipeMaxBytes+8192 {
		return s, b, errors.New("recipe book exceeds bound")
	}
	if err := json.Unmarshal(s.Data, &b); err != nil {
		return s, b, err
	}
	if b.Version != recipeVersion || len(b.Recipes) > recipeLimit {
		return s, b, errors.New("unsupported recipe book")
	}
	return s, b, nil
}
func (m *RecipeMemory) find(ctx context.Context, robot, scope, goal string) (*planningRecipe, bool, error) {
	_, book, err := m.load(ctx, robot)
	if err != nil {
		return nil, false, err
	}
	var best *planningRecipe
	score := 0.55
	for _, r := range book.Recipes {
		wire, _ := json.Marshal(r)
		age := m.now().Sub(r.CreatedAt)
		if r.Scope != scope || age < 0 || age >= recipeTTL || len(wire) > recipeMaxBytes || r.ID != recipeID(r) || len(r.Calls) < 1 || len(r.Calls) > 16 || len(r.Reads) > 6 {
			continue
		}
		if r.Goal == goal {
			return &r, true, nil
		}
		if v := goalSimilarity(goal, r.Goal); v > score {
			best, score = &r, v
		}
	}
	return best, false, nil
}

// A cheap local retrieval score; similarity never authorizes reuse. Chinese
// character bigrams also work without an embedding API or another token bill.
func goalSimilarity(a, b string) float64 {
	grams := func(s string) map[string]bool {
		r := []rune(strings.ToLower(s))
		out := map[string]bool{}
		for i := 1; i < len(r); i++ {
			out[string(r[i-1:i+1])] = true
		}
		return out
	}
	x, y := grams(a), grams(b)
	if len(x) < 3 || len(y) < 3 {
		return 0
	}
	n := 0
	for k := range x {
		if y[k] {
			n++
		}
	}
	return float64(n) / float64(len(x)+len(y)-n)
}
func recipeAudit(verdict string, r *planningRecipe, detail string) capability.PlanningStep {
	result := map[string]any{"advisoryOnly": true, "physicalCompletion": false}
	if r != nil {
		result["recipeId"], result["sourceCreatedAt"] = r.ID, r.CreatedAt
	}
	return capability.PlanningStep{Tool: "planning.recipe", Verdict: verdict, Detail: detail, Result: result}
}
func recipeHint(r *planningRecipe) string {
	if r == nil {
		return ""
	}
	raw, _ := json.Marshal(map[string]any{"recipeId": r.ID, "previousGoal": r.Goal, "candidateCalls": r.Calls, "status": "validated_proposal_only"})
	if len(raw) > 4096 {
		return ""
	}
	return string(raw)
}

// A query that yielded none of its declared semantic fields is no stronger
// evidence than no query. Keep such recipes as hints instead of permitting a
// vacuous equality match to bypass current model reasoning about conditions.
func recipeHasEvidence(r *planningRecipe) bool {
	if len(r.Reads) == 0 {
		return false
	}
	for _, read := range r.Reads {
		if len(read.Result) == 0 {
			return false
		}
	}
	return true
}

func (p *Planner) planWithMemory(ctx context.Context, goal, robot string, entries map[string]capability.Manifest) ([]capability.Call, []capability.PlanningStep, error) {
	if p.Memory == nil {
		return p.modelPlan(ctx, goal, robot, entries)
	}
	m := p.Memory
	scope := p.recipeScope(robot, entries)
	r, exact, lookupErr := m.find(ctx, robot, scope, goal)
	var seed []capability.PlanningStep
	if lookupErr != nil {
		seed = append(seed, recipeAudit("RECIPE_UNAVAILABLE", nil, "memory read failed; normal planning required"))
	} else if r != nil && exact && m.Mode == "validated" && recipeHasEvidence(r) {
		var valid bool
		var checkErr error
		seed, valid, checkErr = p.revalidateRecipe(ctx, goal, robot, entries, r)
		if checkErr != nil {
			return nil, append(seed, recipeAudit("RECIPE_REVALIDATION_BLOCKED", r, "current authority, context or read budget rejected validation")), checkErr
		}
		if valid {
			seed = append(seed, recipeAudit("RECIPE_HIT", r, "exact goal and scope; all planning reads refreshed; proposal revalidated; no GOAL model call"))
			return r.Calls, seed, nil
		}
		seed = append(seed, recipeAudit("RECIPE_REVALIDATION_MISS", r, "current planning evidence or validators changed; normal planning required"))
	} else if r != nil {
		seed = append(seed, recipeAudit("RECIPE_HINT", r, "reference only; current goal requires a new model proposal"))
	}
	if err := ctx.Err(); err != nil {
		return nil, seed, err
	}
	calls, trace, err := p.modelPlanSeeded(ctx, goal, robot, entries, seed, recipeHint(r))
	if err == nil {
		if saveErr := m.remember(ctx, robot, scope, goal, calls, trace); saveErr != nil {
			trace = append(trace, recipeAudit("RECIPE_NOT_SAVED", nil, "proposal remains valid; optional memory write failed"))
		}
	}
	return calls, trace, err
}

// Every read is dispatched again. The old result is only an equality guard;
// only freshly returned READ_OK records are supplied to freezeSemanticRoute.
func (p *Planner) revalidateRecipe(ctx context.Context, goal, robot string, entries map[string]capability.Manifest, r *planningRecipe) ([]capability.PlanningStep, bool, error) {
	var trace []capability.PlanningStep
	for _, old := range r.Reads {
		if err := ctx.Err(); err != nil {
			return trace, false, err
		}
		m, ok := entries[old.Tool]
		if !ok || m.MutatesWorld || len(m.Contract.PlanningFields) == 0 || old.Verdict != "READ_OK" || capability.Validate(old.Arguments, m.InputSchema) != nil {
			return trace, false, nil
		}
		if p.Grants != nil {
			schema, allowed := p.Grants[old.Tool]
			if !allowed || capability.Validate(old.Arguments, schema) != nil {
				return trace, false, nil
			}
		}
		args, err := structpb.NewStruct(old.Arguments)
		if err != nil {
			return trace, false, nil
		}
		step := capability.PlanningStep{Round: len(trace) + 1, Tool: old.Tool, Arguments: old.Arguments, Verdict: "READ_FAILED",
			Detail: "recipe validation read failed; prior result is not current evidence"}
		readCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		response, err := p.Provider.CallService(readCtx, &robotv1.ServiceRequest{RobotId: robot, Name: old.Tool, Parameters: args})
		readContextErr := readCtx.Err()
		cancel()
		if ctx.Err() != nil {
			return append(trace, step), false, ctx.Err()
		}
		if readContextErr != nil {
			return append(trace, step), false, readContextErr
		}
		// Authority/claim/identity checks may return ordinary errors rather than
		// typed transport codes. A failed live read therefore always blocks this
		// invocation; only successful changed evidence can seed model fallback.
		// Optional memory-store failures remain a separate degradable path.
		if err != nil {
			return append(trace, step), false, err
		}
		if response == nil || !response.Ok || response.Result == nil {
			return append(trace, step), false, errors.New("recipe validation provider read rejected or missing structured result")
		}
		result := map[string]any{}
		for _, field := range m.Contract.PlanningFields {
			if v, exists := response.Result.AsMap()[field]; exists {
				result[field] = v
			}
		}
		wire, err := json.Marshal(result)
		if err != nil || len(wire) > 8192 || validatePlanningView(result, 0) != nil {
			step.Verdict, step.Detail = "READ_REJECTED", "invalid current planning view"
			return append(trace, step), false, errors.New("recipe validation returned an invalid planning view")
		}
		step.Verdict, step.Result, step.Detail = "READ_OK", result, ""
		trace = append(trace, step)
		if capability.Fingerprint(result) != capability.Fingerprint(old.Result) {
			return trace, false, nil
		}
	}
	return trace, p.freezeCalls(r.Calls, goal, robot, entries, trace...) == nil, nil
}

func (m *RecipeMemory) remember(ctx context.Context, robot, scope, goal string, calls []capability.Call, trace []capability.PlanningStep) error {
	r := planningRecipe{Scope: scope, Goal: goal, CreatedAt: m.now().UTC()}
	for _, c := range calls {
		// Composite intents are re-derived by today's parser, never stored as an
		// executable task. Command IDs, approvals and receipts are not retained.
		r.Calls = append(r.Calls, capability.Call{Tool: c.Tool, Arguments: c.Arguments})
	}
	for _, s := range trace {
		if s.Tool == "planning.recipe" || s.Verdict == "FROZEN_FOR_APPROVAL" {
			continue
		}
		if s.Verdict != "READ_OK" {
			return nil
		} // no recipes from uncertain/failed reads or repaired proposals
		r.Reads = append(r.Reads, capability.PlanningStep{Tool: s.Tool, Arguments: s.Arguments, Result: s.Result, Verdict: "READ_OK"})
	}
	if len(r.Reads) > 6 {
		return nil
	}
	r.ID = recipeID(r)
	wire, err := json.Marshal(r)
	if err != nil || len(wire) > recipeMaxBytes {
		return nil
	}
	// Clone the retained proposal; later validation must not mutate its digest.
	if err := json.Unmarshal(wire, &r); err != nil {
		return err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	for attempt := 0; attempt < 3; attempt++ {
		s, book, err := m.load(ctx, robot)
		if err != nil {
			return err
		}
		keep := []planningRecipe{r}
		for _, old := range book.Recipes {
			if old.Scope == scope && old.Goal == goal {
				continue
			}
			age := m.now().Sub(old.CreatedAt)
			if age >= 0 && age < recipeTTL {
				keep = append(keep, old)
			}
		}
		sort.SliceStable(keep, func(i, j int) bool { return keep[i].CreatedAt.After(keep[j].CreatedAt) })
		if len(keep) > recipeLimit {
			keep = keep[:recipeLimit]
		}
		data, _ := json.Marshal(recipeBook{Version: recipeVersion, Recipes: keep})
		err = m.Store.Commit(ctx, eventstore.CommitRequest{ExpectedVersion: s.Version, State: eventstore.AggregateState{AggregateID: m.key(robot), Version: s.Version + 1, Data: data, UpdatedAt: m.now().UTC()}})
		if err == nil {
			return nil
		}
		if !errors.Is(err, eventstore.ErrVersionConflict) {
			return err
		}
	}
	return fmt.Errorf("recipe update conflicted repeatedly")
}
