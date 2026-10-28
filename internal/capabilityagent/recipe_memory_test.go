package capabilityagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// All reads and model decisions in these tests stay in process. No task is
// approved, no external provider/model is contacted, and mapping.build must
// remain a proposal rather than a physical command.
type recipeProvider struct {
	fakeProvider
	robot, catalogNote string
	reads              int
	view               map[string]any
	readErr            error
	refusal            string
	onRead             func(int)
}

func newRecipeProvider() *recipeProvider {
	return &recipeProvider{robot: "recipe-robot", view: map[string]any{
		"revision": "cal-1", "state": "ready",
		"activeMap":   map[string]any{"mapId": "map-1", "mapRevision": "map-rev-1"},
		"imageBase64": "unprojected-private-image",
	}}
}

func (p *recipeProvider) ListServices(ctx context.Context) (*robotv1.ServiceCatalog, error) {
	catalog, err := p.fakeProvider.ListServices(ctx)
	catalog.RobotId = p.robot
	catalog.Services[0].Description = p.catalogNote
	return catalog, err
}

func (p *recipeProvider) CallService(ctx context.Context, req *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	if req.RobotId != p.robot {
		return nil, errors.New("fixture received another robot identity")
	}
	if req.Name != "calibration.get" {
		return p.fakeProvider.CallService(ctx, req)
	}
	p.reads++
	if p.onRead != nil {
		p.onRead(p.reads)
	}
	if p.readErr != nil {
		return nil, p.readErr
	}
	if p.refusal != "" {
		return &robotv1.ServiceResponse{Code: p.refusal, Message: "request denied by current runtime"}, nil
	}
	return &robotv1.ServiceResponse{Ok: true, Result: fields(p.view)}, nil
}

func recipeProposal() actionloop.Decision {
	return actionloop.Decision{Tool: "propose_capability_plan", Arguments: map[string]any{"calls": []any{
		map[string]any{"tool": "mapping.build", "arguments": map[string]any{"mode": "explore"}},
	}}}
}

func recipeMemoryFixture(t *testing.T, mode string) (*Planner, *recipeProvider, *sqlite.Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "recipes.db")
	store, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	memory, err := NewRecipeMemory(store, "test-model-template", mode)
	if err != nil {
		t.Fatal(err)
	}
	if memory != nil {
		memory.now = func() time.Time { return time.Unix(1800000000, 0) }
	}
	provider := newRecipeProvider()
	planner := &Planner{Provider: provider, Memory: memory}
	return planner, provider, store, path
}

func trainRecipe(t *testing.T, planner *Planner, goal string, reads int) orchestration.Bundle {
	t.Helper()
	decisions := 0
	planner.Decider = contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
		decisions++
		if decisions <= reads {
			return actionloop.Decision{Tool: "calibration.get", Arguments: map[string]any{}}, nil
		}
		return recipeProposal(), nil
	})
	bundle, handled, err := planner.PlanGoal(context.Background(), goal)
	if err != nil || !handled || bundle.Capabilities == nil || len(bundle.Capabilities.Calls) != 1 || decisions != reads+1 {
		t.Fatalf("training: handled=%v decisions=%d bundle=%+v err=%v", handled, decisions, bundle, err)
	}
	return bundle
}

func recipeVerdict(trace []capability.PlanningStep, verdict string) bool {
	for _, step := range trace {
		if step.Tool == "planning.recipe" && step.Verdict == verdict {
			return true
		}
	}
	return false
}

func TestRecipeExactReuseRefreshesEvidenceAfterSQLiteRestartWithoutExecuting(t *testing.T) {
	planner, provider, store, path := recipeMemoryFixture(t, "validated")
	goal := "检查当前条件，然后建立地图"
	first := trainRecipe(t, planner, goal, 1)
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	memory, err := NewRecipeMemory(reopened, "test-model-template", "validated")
	if err != nil {
		t.Fatal(err)
	}
	memory.now = planner.Memory.now
	planner.Memory = memory
	modelCalls := 0
	planner.Decider = contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
		modelCalls++
		return recipeProposal(), nil
	})
	bundle, handled, err := planner.PlanGoal(context.Background(), goal)
	if err != nil || !handled || len(bundle.Capabilities.Calls) != 1 || modelCalls != 0 || provider.reads != 2 || provider.starts != 0 {
		t.Fatalf("reuse model=%d reads=%d writes=%d handled=%v err=%v", modelCalls, provider.reads, provider.starts, handled, err)
	}
	trace := bundle.Capabilities.PlanningTrace
	if len(trace) != 2 || trace[0].Verdict != "READ_OK" || trace[0].Context != nil || !recipeVerdict(trace, "RECIPE_HIT") ||
		trace[1].Result["advisoryOnly"] != true || trace[1].Result["physicalCompletion"] != false {
		t.Fatalf("reuse provenance/evidence=%+v", trace)
	}
	if capability.Fingerprint(first.Capabilities.Calls) != capability.Fingerprint(bundle.Capabilities.Calls) {
		t.Fatal("unchanged validated recipe changed proposal")
	}
	state, found, err := reopened.LoadState(context.Background(), memory.key(provider.robot))
	if err != nil || !found || state.Version != 1 || strings.Contains(string(state.Data), "unprojected-private-image") || strings.Contains(string(state.Data), "contextSnapshot") {
		t.Fatalf("retained recipe contains private context or reuse refreshed TTL: version=%d found=%v err=%v", state.Version, found, err)
	}
}

func TestRecipeHTTPFixtureReuseEliminatesModelRequestsButRefreshesRead(t *testing.T) {
	planner, provider, _, _ := recipeMemoryFixture(t, "validated")
	provider.catalogNote = "fixture calibration semantic read"
	var requests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := requests.Add(1)
		var request struct {
			Tools []struct {
				Function struct{ Name, Description string }
			}
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		tool := ""
		for _, candidate := range request.Tools {
			if candidate.Function.Name != "propose_capability_plan" && strings.Contains(candidate.Function.Description, provider.catalogNote) {
				tool = candidate.Function.Name // Honor the actual wire alias allowlist.
				break
			}
		}
		arguments := map[string]any{}
		if n > 1 {
			proposal := recipeProposal()
			tool, arguments = proposal.Tool, proposal.Arguments
		}
		encoded, err := json.Marshal(arguments)
		if err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{
			"tool_calls": []any{map[string]any{"id": fmt.Sprintf("fixture-%d", n), "type": "function",
				"function": map[string]any{"name": tool, "arguments": string(encoded)}}},
		}}}})
	}))
	defer server.Close()
	planner.Decider = &actionloop.LLMDecider{BaseURL: server.URL, Model: "in-process-fixture-model"}
	goal := "检查当前条件，然后建立地图"
	first, handled, err := planner.PlanGoal(context.Background(), goal)
	if err != nil || !handled || first.Capabilities == nil || requests.Load() != 2 || provider.reads != 1 {
		t.Fatalf("initial planning: requests=%d reads=%d handled=%v bundle=%+v err=%v", requests.Load(), provider.reads, handled, first, err)
	}
	initialRequests, initialReads := requests.Load(), provider.reads
	second, handled, err := planner.PlanGoal(context.Background(), goal)
	reuseRequests, reuseReads := requests.Load()-initialRequests, provider.reads-initialReads
	if err != nil || !handled || reuseRequests != 0 || reuseReads != 1 || provider.starts != 0 || !recipeVerdict(second.Capabilities.PlanningTrace, "RECIPE_HIT") {
		t.Fatalf("exact reuse: requests=%d reads=%d writes=%d handled=%v err=%v", reuseRequests, reuseReads, provider.starts, handled, err)
	}
	for _, step := range second.Capabilities.PlanningTrace {
		if step.Context != nil {
			t.Fatal("recipe hit retained an old model request/context as a new call")
		}
	}
	record, _ := json.Marshal(map[string]any{
		"schemaVersion": "recipe.fixture.experiment.v1", "fixture": "in-process-http-and-provider",
		"realLLM": false, "gazebo": false, "realRobot": false,
		"baselineModelHTTPRequests": initialRequests, "reuseModelHTTPRequests": reuseRequests,
		"baselineFreshProviderReads": initialReads, "reuseFreshProviderReads": reuseReads,
		"physicalWrites": provider.starts, "physicalCompletion": false, "verdict": "RECIPE_HIT",
	})
	t.Logf("RECIPE_EXPERIMENT %s", record)
}

func TestRecipeComparesAllDeclaredSemanticFieldsAndUsesFreshFallbackEvidence(t *testing.T) {
	planner, provider, _, _ := recipeMemoryFixture(t, "validated")
	goal := "检查当前条件，然后建立地图"
	trainRecipe(t, planner, goal, 1)
	// The advertised revision is unchanged. Nested semantic state must still be
	// compared rather than trusting a short summary or revision alone.
	provider.view["activeMap"].(map[string]any)["mapRevision"] = "map-rev-2"
	modelCalls := 0
	planner.Decider = contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
		modelCalls++
		if req.Round != 2 || len(req.History) != 1 || req.History[0].ResultDetail["activeMap"].(map[string]any)["mapRevision"] != "map-rev-2" {
			t.Fatalf("fallback failed to bind fresh evidence: %+v", req.History)
		}
		return recipeProposal(), nil
	})
	bundle, _, err := planner.PlanGoal(context.Background(), goal)
	if err != nil || modelCalls != 1 || provider.reads != 2 || provider.starts != 0 || !recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_REVALIDATION_MISS") || recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_HIT") {
		t.Fatalf("changed semantic field reused old proposal: model=%d reads=%d err=%v", modelCalls, provider.reads, err)
	}
}

func TestRecipeScopeSeparatesRobotModelCatalogGrantsAndProgress(t *testing.T) {
	for _, change := range []string{"robot", "model", "catalog", "grants", "progress"} {
		t.Run(change, func(t *testing.T) {
			planner, provider, _, _ := recipeMemoryFixture(t, "validated")
			goal := "检查当前条件，然后建立地图"
			trainRecipe(t, planner, goal, 1)
			switch change {
			case "robot":
				provider.robot = "another-robot"
			case "model":
				planner.Memory.Namespace = "another-model-template"
			case "catalog":
				provider.catalogNote = "current catalog contract description"
			case "grants":
				_, entries, err := Catalogue(context.Background(), provider)
				if err != nil {
					t.Fatal(err)
				}
				planner.Grants = map[string]map[string]any{}
				for name, entry := range entries {
					planner.Grants[name] = entry.InputSchema
				}
			case "progress":
				planner.Progress = "Only one goal remains; previous writes already confirmed."
			}
			modelCalls := 0
			planner.Decider = contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
				modelCalls++
				if req.Round != 1 || len(req.History) != 0 {
					t.Fatal("old-scope planning reads crossed the boundary")
				}
				return recipeProposal(), nil
			})
			bundle, _, err := planner.PlanGoal(context.Background(), goal)
			if err != nil || modelCalls != 1 || provider.reads != 1 || recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_HIT") || recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_HINT") {
				t.Fatalf("scope %s reused data: model=%d reads=%d err=%v", change, modelCalls, provider.reads, err)
			}
		})
	}
}

func TestRecipeZeroReadSimilarGoalAndHintModeAlwaysConsultModel(t *testing.T) {
	for _, mode := range []string{"zero-read", "empty-view", "similar-goal", "hint-mode"} {
		t.Run(mode, func(t *testing.T) {
			planner, provider, _, _ := recipeMemoryFixture(t, "validated")
			goal := "检查当前条件，然后建立地图"
			reads := 1
			if mode == "zero-read" {
				reads = 0
			}
			if mode == "empty-view" {
				provider.view = map[string]any{"imageBase64": "unprojected-private-image"}
			}
			trainRecipe(t, planner, goal, reads)
			if mode == "similar-goal" {
				goal += "并检查结果"
			}
			if mode == "hint-mode" {
				planner.Memory.Mode = "hint"
			}
			modelCalls := 0
			planner.Decider = contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
				modelCalls++
				if req.Round != 1 || len(req.History) != 0 {
					t.Fatal("advisory recipe became current planning evidence")
				}
				hintFound := false
				for _, record := range req.Observation.Context.Records {
					if record.ID == "planning:recipe" {
						hintFound = record.Kind == "reference" && strings.Contains(record.Statement, "validated_proposal_only")
					}
				}
				if !hintFound {
					t.Fatal("bounded recipe reference absent")
				}
				return recipeProposal(), nil
			})
			bundle, _, err := planner.PlanGoal(context.Background(), goal)
			if err != nil || modelCalls != 1 || provider.reads != reads || !recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_HINT") || recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_HIT") {
				t.Fatalf("%s skipped current model: model=%d reads=%d err=%v", mode, modelCalls, provider.reads, err)
			}
		})
	}
}

func TestRecipeSixRevalidationReadsConsumeOriginalPlanningBudget(t *testing.T) {
	planner, provider, _, _ := recipeMemoryFixture(t, "validated")
	goal := "检查当前条件，然后建立地图"
	trainRecipe(t, planner, goal, 6)
	provider.onRead = func(count int) {
		if count == 12 {
			provider.view["state"] = "changed-on-last-read"
		}
	}
	modelCalls := 0
	planner.Decider = contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
		modelCalls++
		if req.Round != 6+modelCalls || planningToolOffered(req, "calibration.get") {
			t.Fatalf("fallback reset six-read budget: round=%d modelCalls=%d", req.Round, modelCalls)
		}
		if modelCalls == 1 {
			// A custom model selecting the now-removed read still cannot dispatch.
			return actionloop.Decision{Tool: "calibration.get", Arguments: map[string]any{}}, nil
		}
		return recipeProposal(), nil
	})
	bundle, _, err := planner.PlanGoal(context.Background(), goal)
	if err != nil || modelCalls != 2 || provider.reads != 12 || provider.starts != 0 || !recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_REVALIDATION_MISS") {
		t.Fatalf("budget bypass: reads=%d model=%d writes=%d err=%v", provider.reads, modelCalls, provider.starts, err)
	}
}

func TestRecipeAuthorityAndContextRefusalsNeverFallbackToModel(t *testing.T) {
	for _, failure := range []string{"permission", "unauthenticated", "read-budget", "outcome-unknown", "canceled", "deadline", "untyped-authority", "transport-unavailable", "response-permission", "response-unauthenticated", "invalid-view"} {
		t.Run(failure, func(t *testing.T) {
			planner, provider, _, _ := recipeMemoryFixture(t, "validated")
			goal := "检查当前条件，然后建立地图"
			trainRecipe(t, planner, goal, 1)
			switch failure {
			case "permission":
				provider.readErr = status.Error(codes.PermissionDenied, "current permission revoked")
			case "unauthenticated":
				provider.readErr = status.Error(codes.Unauthenticated, "current identity rejected")
			case "read-budget":
				provider.readErr = fmt.Errorf("%w: objective reads exhausted", ErrNeedsInput)
			case "outcome-unknown":
				provider.readErr = ErrOutcomeUnknown
			case "canceled":
				provider.readErr = context.Canceled
			case "deadline":
				provider.readErr = context.DeadlineExceeded
			case "untyped-authority":
				provider.readErr = errors.New("objective service identity mismatch")
			case "transport-unavailable":
				provider.readErr = status.Error(codes.Unavailable, "fixture transport interrupted")
			case "response-permission":
				provider.refusal = "PERMISSION_DENIED"
			case "response-unauthenticated":
				provider.refusal = "UNAUTHENTICATED"
			case "invalid-view":
				provider.view["activeMap"] = map[string]any{"pointcloud": "raw secret data"}
			}
			modelCalls := 0
			planner.Decider = contextPlanningDecider(func(actionloop.Request) (actionloop.Decision, error) {
				modelCalls++
				return recipeProposal(), nil
			})
			bundle, handled, err := planner.PlanGoal(context.Background(), goal)
			if err == nil || !handled || modelCalls != 0 || provider.reads != 2 || provider.starts != 0 || len(bundle.Capabilities.Calls) != 0 || !recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_REVALIDATION_BLOCKED") {
				t.Fatalf("%s denial bypassed: model=%d reads=%d handled=%v err=%v", failure, modelCalls, provider.reads, handled, err)
			}
			if provider.readErr != nil && !errors.Is(err, provider.readErr) {
				t.Fatalf("caller lost the original stop/reconciliation error: want=%v got=%v", provider.readErr, err)
			}
			trace := bundle.Capabilities.PlanningTrace
			wantVerdict := "READ_FAILED"
			if failure == "invalid-view" {
				wantVerdict = "READ_REJECTED"
			}
			if len(trace) != 2 || trace[0].Tool != "calibration.get" || trace[0].Verdict != wantVerdict {
				t.Fatalf("denied validation read missing from audit: %+v", trace)
			}
		})
	}
}

type recipeStateOverride struct {
	eventstore.Store
	state eventstore.AggregateState
}

func (s recipeStateOverride) LoadState(context.Context, string) (eventstore.AggregateState, bool, error) {
	return s.state, true, nil
}

func TestRecipeCorruptStateCannotCrossStoreIdentityOrExtendTTL(t *testing.T) {
	for _, corruption := range []string{"foreign-aggregate", "zero-version", "malformed-json", "changed-created-at", "changed-calls", "unknown-version"} {
		t.Run(corruption, func(t *testing.T) {
			planner, provider, store, _ := recipeMemoryFixture(t, "validated")
			goal := "检查当前条件，然后建立地图"
			trainRecipe(t, planner, goal, 1)
			state, book, err := planner.Memory.load(context.Background(), provider.robot)
			if err != nil {
				t.Fatal(err)
			}
			switch corruption {
			case "foreign-aggregate":
				state.AggregateID = "planning-recipes/another-robot"
			case "zero-version":
				state.Version = 0
			case "malformed-json":
				state.Data = []byte("{broken")
			case "changed-created-at":
				book.Recipes[0].CreatedAt = book.Recipes[0].CreatedAt.Add(-time.Hour)
				state.Data, _ = json.Marshal(book)
			case "changed-calls":
				book.Recipes[0].Calls[0].Tool = "another.tool"
				state.Data, _ = json.Marshal(book)
			case "unknown-version":
				book.Version = "future-recipe.v2"
				state.Data, _ = json.Marshal(book)
			}
			planner.Memory.Store = recipeStateOverride{Store: store, state: state}
			modelCalls := 0
			planner.Decider = contextPlanningDecider(func(req actionloop.Request) (actionloop.Decision, error) {
				modelCalls++
				if len(req.History) != 0 {
					t.Fatal("invalid stored evidence entered fresh planning history")
				}
				return recipeProposal(), nil
			})
			bundle, _, err := planner.PlanGoal(context.Background(), goal)
			if err != nil || modelCalls != 1 || provider.reads != 1 || recipeVerdict(bundle.Capabilities.PlanningTrace, "RECIPE_HIT") {
				t.Fatalf("corruption %s escaped validation: model=%d reads=%d err=%v", corruption, modelCalls, provider.reads, err)
			}
		})
	}
}

func TestRecipeNamespaceAndConfigurationRespectIsolation(t *testing.T) {
	base := modelroute.Endpoint{Provider: "openai", BaseURL: "https://example.invalid/v1", Model: "model-a", APIKey: "secret-a", Cache: modelroute.CachePolicy{Namespace: "deployment-a"}}
	key := RecipeNamespace(base)
	changedKey := base
	changedKey.APIKey = "secret-b"
	changedKey.BaseURL += "/"
	if RecipeNamespace(changedKey) != key || strings.Contains(key, "secret") {
		t.Fatal("credentials or trailing URL slash changed namespace")
	}
	for _, change := range []string{"model", "provider", "origin", "namespace"} {
		ep := base
		switch change {
		case "model":
			ep.Model = "model-b"
		case "provider":
			ep.Provider = "another-provider"
		case "origin":
			ep.BaseURL = "https://another.invalid/v1"
		case "namespace":
			ep.Cache.Namespace = "deployment-b"
		}
		if RecipeNamespace(ep) == key {
			t.Fatalf("namespace did not isolate %s", change)
		}
	}
	for _, mode := range []string{"", "off"} {
		if memory, err := NewRecipeMemory(nil, "", mode); err != nil || memory != nil {
			t.Fatalf("disabled memory requires storage: %q %v", mode, err)
		}
	}
	for _, mode := range []string{"hint", "validated", "typo"} {
		if _, err := NewRecipeMemory(nil, "", mode); err == nil {
			t.Fatalf("invalid memory configuration accepted: %q", mode)
		}
	}
}

type failingRecipeStore struct {
	eventstore.Store
	loadErr, commitErr error
	commits            int
}

func (s *failingRecipeStore) LoadState(ctx context.Context, key string) (eventstore.AggregateState, bool, error) {
	if s.loadErr != nil {
		return eventstore.AggregateState{}, false, s.loadErr
	}
	return s.Store.LoadState(ctx, key)
}

func (s *failingRecipeStore) Commit(ctx context.Context, request eventstore.CommitRequest) error {
	s.commits++
	if s.commitErr != nil {
		return s.commitErr
	}
	return s.Store.Commit(ctx, request)
}

func TestRecipeOptionalStoreFailureNeverRepeatsPlanningOrLeaksStoreError(t *testing.T) {
	for _, mode := range []string{"read", "write", "conflicts"} {
		t.Run(mode, func(t *testing.T) {
			planner, provider, store, _ := recipeMemoryFixture(t, "validated")
			failing := &failingRecipeStore{Store: store}
			switch mode {
			case "read":
				failing.loadErr = errors.New("secret-storage-detail")
			case "write":
				failing.commitErr = errors.New("secret-storage-detail")
			case "conflicts":
				failing.commitErr = eventstore.ErrVersionConflict
			}
			planner.Memory.Store = failing
			bundle := trainRecipe(t, planner, "检查当前条件，然后建立地图", 1)
			trace := bundle.Capabilities.PlanningTrace
			wire, _ := json.Marshal(trace)
			if !recipeVerdict(trace, "RECIPE_NOT_SAVED") || strings.Contains(string(wire), "secret-storage-detail") || provider.reads != 1 || provider.starts != 0 {
				t.Fatalf("optional store changed plan or leaked error: reads=%d writes=%d", provider.reads, provider.starts)
			}
			wantCommits := 1
			if mode == "read" {
				wantCommits = 0
				if !recipeVerdict(trace, "RECIPE_UNAVAILABLE") {
					t.Fatal("lookup degradation not audited")
				}
			} else if mode == "conflicts" {
				wantCommits = 3
			}
			if failing.commits != wantCommits {
				t.Fatalf("unexpected commit retries: got=%d want=%d", failing.commits, wantCommits)
			}
		})
	}
}

func TestRecipeRetentionHasHardCountTTLAndNoSlidingExpiry(t *testing.T) {
	planner, provider, _, _ := recipeMemoryFixture(t, "validated")
	ctx := context.Background()
	_, entries, err := Catalogue(ctx, provider)
	if err != nil {
		t.Fatal(err)
	}
	scope := planner.recipeScope(provider.robot, entries)
	calls := []capability.Call{{Tool: "mapping.build", Arguments: map[string]any{"mode": "explore"}}}
	start := planner.Memory.now()
	for i := 0; i < 40; i++ {
		now := start.Add(time.Duration(i) * time.Minute)
		planner.Memory.now = func() time.Time { return now }
		if err := planner.Memory.remember(ctx, provider.robot, scope, fmt.Sprintf("mission-%02d", i), calls, nil); err != nil {
			t.Fatal(err)
		}
	}
	state, book, err := planner.Memory.load(ctx, provider.robot)
	if err != nil || len(book.Recipes) != 32 || state.Version != 40 || len(state.Data) > recipeLimit*recipeMaxBytes+8192 || book.Recipes[0].Goal != "mission-39" || book.Recipes[31].Goal != "mission-08" {
		t.Fatalf("retention count/order/version incorrect: recipes=%d version=%d err=%v", len(book.Recipes), state.Version, err)
	}
	created := book.Recipes[0].CreatedAt
	planner.Memory.now = func() time.Time { return created.Add(recipeTTL - time.Nanosecond) }
	if r, exact, err := planner.Memory.find(ctx, provider.robot, scope, "mission-39"); err != nil || !exact || r == nil {
		t.Fatalf("fresh recipe unavailable: exact=%v err=%v", exact, err)
	}
	planner.Memory.now = func() time.Time { return created.Add(recipeTTL) }
	if r, _, err := planner.Memory.find(ctx, provider.robot, scope, "mission-39"); err != nil || r != nil {
		t.Fatalf("lookup refreshed expiry or retained expired recipe: %+v %v", r, err)
	}
	planner.Memory.now = func() time.Time { return start.Add(-time.Second) }
	if r, _, err := planner.Memory.find(ctx, provider.robot, scope, "mission-39"); err != nil || r != nil {
		t.Fatalf("future-dated recipe accepted after clock rollback: %+v %v", r, err)
	}
}
