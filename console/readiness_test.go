package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Readiness is the answer to "can I use this robot right now", and the console
// did not have one: /healthz returned a constant and /v1/runtime reported a
// socket. A robot in an emergency stop with no map and an unknown-outcome step
// reported ok.
//
// These tests are about the promise the report makes: it may not say ready
// without evidence, it may not say a problem is unfixable without saying what to
// do, and it may not turn "I could not check" into either answer.

type readinessCheckView struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	State     string `json:"state"`
	Situation string `json:"situation"`
	Action    string `json:"action"`
	Detail    string `json:"detail"`
	Blocking  bool   `json:"blocking"`
}

type readinessView struct {
	Ready             bool `json:"ready"`
	Summary           string
	NextID            string `json:"nextId"`
	Checks            []readinessCheckView
	UnresolvedOutcome int `json:"unresolvedOutcome"`
	Language          struct {
		Provider        string `json:"provider"`
		ModelConfigured bool   `json:"modelConfigured"`
		Vocabulary      string `json:"vocabulary"`
		Note            string `json:"note"`
	} `json:"language"`
}

func getReadiness(t *testing.T, server *httptest.Server) readinessView {
	t.Helper()
	response, err := http.Get(server.URL + "/v1/readiness")
	if err != nil {
		t.Fatalf("get readiness: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", response.StatusCode)
	}
	var view readinessView
	if err := json.NewDecoder(response.Body).Decode(&view); err != nil {
		t.Fatalf("decode readiness: %v", err)
	}
	return view
}

func checkByID(view readinessView, id string) readinessCheckView {
	for _, check := range view.Checks {
		if check.ID == id {
			return check
		}
	}
	return readinessCheckView{}
}

// readinessServer builds a console with a robot that answers and an executor that
// supervises. Individual tests vary one signal by poking the returned values.
type fakeRuntime struct {
	snapshot runtime.Snapshot
	err      error
}

func (f fakeRuntime) Info(context.Context) (runtime.Snapshot, error) {
	return f.snapshot, f.err
}

func readinessServer(t *testing.T, robot runtime.Snapshot, robotErr error, supervision tasks.SupervisionStatus) *httptest.Server {
	t.Helper()
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	app := localapp.New(service, agent.NewRunner(nil, nil, nil), memory.NewQueue[string](4))
	app.WithSupervision(func() tasks.SupervisionStatus { return supervision })
	server := httptest.NewServer(console.NewServer(service, app,
		console.WithRuntime(fakeRuntime{snapshot: robot, err: robotErr}),
	).Handler())
	t.Cleanup(server.Close)
	return server
}

// healthyRobot is what a robot that can be used reports about itself.
func healthyRobot() runtime.Snapshot {
	return runtime.Snapshot{
		RobotID: "xlerobot-0001", Ready: true,
		Capabilities: []runtime.Capability{
			{Name: "navigation.navigate", Available: true},
			{Name: "manipulation.pick", Available: true},
			{Name: "emergency_stop", Available: true},
		},
	}
}

func watching() tasks.SupervisionStatus {
	return tasks.SupervisionStatus{Enabled: true, Observing: true, Agents: []string{"task", "ops", "recovery"}}
}

func TestAReadyRobotSaysSo(t *testing.T) {
	server := readinessServer(t, healthyRobot(), nil, watching())
	view := getReadiness(t, server)
	if !view.Ready {
		t.Fatalf("a robot reporting itself ready was reported as not ready: %+v", view.Checks)
	}
	if view.NextID != "" {
		t.Fatalf("nextId = %q on a ready robot", view.NextID)
	}
	if view.Summary != "机器人可以用了。" {
		t.Fatalf("summary = %q", view.Summary)
	}
	for _, check := range view.Checks {
		if check.State == "action" {
			t.Fatalf("a ready robot produced an action item: %+v", check)
		}
	}
}

// The defect, as an assertion: a robot that reports a blocking fault is not ready,
// however healthy the process is.
func TestARobotWithABlockingFaultIsNotReady(t *testing.T) {
	robot := healthyRobot()
	robot.Ready = false
	robot.Blockers = []string{"chassis:NAV_MAP_NOT_READY"}
	robot.Capabilities = []runtime.Capability{
		{Name: "navigation.navigate", Available: false, Blockers: []string{"chassis:NAV_MAP_NOT_READY"}},
		{Name: "manipulation.pick", Available: true},
	}
	view := getReadiness(t, readinessServer(t, robot, nil, watching()))

	if view.Ready {
		t.Fatal("a robot with a blocking fault was reported as usable")
	}
	if checkByID(view, "map").State != "action" {
		t.Fatalf("the map check did not reflect the robot's own NAV_MAP_NOT_READY: %+v", checkByID(view, "map"))
	}
	if checkByID(view, "faults").Detail == "" {
		t.Fatal("the fault check named no fault, so nothing can be looked up")
	}
}

// An emergency stop is a reason not to use the robot and not a reason to restart
// the process, which is why liveness and readiness are separate answers.
func TestAnEmergencyStopBlocksUseAndSaysWhoCanClearIt(t *testing.T) {
	robot := healthyRobot()
	robot.Ready = false
	robot.Blockers = []string{"estop:EMERGENCY_STOP_LATCHED"}
	view := getReadiness(t, readinessServer(t, robot, nil, watching()))

	check := checkByID(view, "safety")
	if check.State != "action" {
		t.Fatalf("safety check = %+v with a latched stop", check)
	}
	// No software path may release the stop, so the instruction has to name the
	// physical act. Offering a button that does not exist would be worse than
	// saying nothing.
	if check.Action == "" {
		t.Fatal("the safety check does not say what to do")
	}
	if view.Ready {
		t.Fatal("a robot in an emergency stop was reported as usable")
	}
}

// A failed connection is evidence about the link, so it is a problem to fix
// rather than an unknown. The distinction matters: "unknown" tells an owner
// nothing to do, and here there is something to do.
func TestARobotThatCannotBeReachedIsAProblemWithANextStep(t *testing.T) {
	server := readinessServer(t, runtime.Snapshot{}, context.DeadlineExceeded, watching())
	view := getReadiness(t, server)

	link := checkByID(view, "robot")
	if link.State != "action" {
		t.Fatalf("link check = %+v after a failed connection attempt", link)
	}
	if link.Action == "" {
		t.Fatal("the link check does not say what to try")
	}
	if link.Detail == "" {
		t.Fatal("the link check does not carry why it could not be reached")
	}
	if view.Ready {
		t.Fatal("an unreachable robot was reported as usable")
	}
	// It must not be reported as a fault: nothing was learned about the robot's
	// hardware, only about the network path to it.
	if checkByID(view, "faults").State == "action" {
		t.Fatal("an unreachable robot was reported as having a fault")
	}
}

// Not being able to ask at all is the unknown case, and it must not be shown as
// a problem either: nothing was learned, in an either direction.
func TestARobotThatWasNeverAskedIsUnknownRatherThanBroken(t *testing.T) {
	store := tasks.NewMemoryStore()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	app := localapp.New(service, agent.NewRunner(nil, nil, nil), memory.NewQueue[string](4))
	app.WithSupervision(watching)
	// No runtime provider: the console was never given a way to ask.
	server := httptest.NewServer(console.NewServer(service, app).Handler())
	t.Cleanup(server.Close)

	view := getReadiness(t, server)
	link := checkByID(view, "robot")
	if link.State != "unknown" {
		t.Fatalf("link check = %+v when the console has no way to ask the robot", link)
	}
	if view.Ready {
		t.Fatal("a robot that was never asked was reported as usable")
	}
}

// An unwatched robot is not a usable robot in the sense this product promises:
// the diagnosis story is void if the diagnosing agent is off, and nothing else
// would say so.
func TestARobotNobodyIsWatchingIsNotReady(t *testing.T) {
	view := getReadiness(t, readinessServer(t, healthyRobot(), nil, tasks.SupervisionStatus{}))
	check := checkByID(view, "supervision")
	if check.State != "action" {
		t.Fatalf("supervision check = %+v with the observer switched off", check)
	}
	if !check.Blocking {
		t.Fatal("an unwatched robot is reported as non-blocking")
	}
	if view.Ready {
		t.Fatal("a robot nobody is watching was reported as usable")
	}
}

// The natural-language capability has to be visible, because "it did not
// understand me" and "no model is configured" look identical from outside.
func TestTheLanguageCapabilityIsStatedPlainly(t *testing.T) {
	view := getReadiness(t, readinessServer(t, healthyRobot(), nil, watching()))
	if view.Language.ModelConfigured {
		t.Fatal("a console with no model reported one as configured")
	}
	if view.Language.Vocabulary == "" {
		t.Fatal("the report does not say what is understood without a model")
	}
	if view.Language.Note == "" {
		t.Fatal("the report does not explain the consequence of having no model")
	}
}

// The list leads with what blocks, and one row is always the useful one.
func TestTheBlockingProblemIsFirstAndNamed(t *testing.T) {
	robot := healthyRobot()
	robot.Ready = false
	robot.Blockers = []string{"chassis:NAV_MAP_NOT_READY"}
	robot.Capabilities = []runtime.Capability{
		{Name: "navigation.navigate", Available: false, Blockers: []string{"chassis:NAV_MAP_NOT_READY"}},
	}
	view := getReadiness(t, readinessServer(t, robot, nil, tasks.SupervisionStatus{}))

	if view.NextID == "" {
		t.Fatal("a robot with two blockers named no next step")
	}
	if view.Checks[0].State != "action" || !view.Checks[0].Blocking {
		t.Fatalf("the first row is not a blocking problem: %+v", view.Checks[0])
	}
	if view.Summary == "" || view.Summary == "机器人可以用了。" {
		t.Fatalf("summary = %q", view.Summary)
	}
	// Every blocking problem must say what to do, or the report is a complaint
	// rather than an instruction.
	for _, check := range view.Checks {
		if check.State == "action" && check.Action == "" {
			t.Fatalf("check %s reports a problem and no way to act on it", check.ID)
		}
	}
}

// The report is stable between two reads of the same state, so a console polling
// it does not show the list reshuffling.
func TestTwoReadsOfTheSameStateAgree(t *testing.T) {
	server := readinessServer(t, healthyRobot(), nil, watching())
	first := getReadiness(t, server)
	second := getReadiness(t, server)
	if len(first.Checks) != len(second.Checks) {
		t.Fatalf("two reads disagreed on the number of checks: %d vs %d", len(first.Checks), len(second.Checks))
	}
	for index := range first.Checks {
		if first.Checks[index].ID != second.Checks[index].ID {
			t.Fatalf("check order changed between reads: %s then %s",
				first.Checks[index].ID, second.Checks[index].ID)
		}
	}
}

// Liveness keeps answering ok while readiness says no. Two questions, two
// answers, and a supervisor that restarted the process over a latched stop would
// be causing an outage with a working safety feature.
func TestLivenessStaysOkWhileReadinessSaysNo(t *testing.T) {
	robot := healthyRobot()
	robot.Ready = false
	robot.Blockers = []string{"estop:EMERGENCY_STOP_LATCHED"}
	server := readinessServer(t, robot, nil, watching())

	response, err := http.Get(server.URL + "/healthz")
	if err != nil {
		t.Fatalf("get healthz: %v", err)
	}
	defer response.Body.Close()
	var health map[string]string
	if err := json.NewDecoder(response.Body).Decode(&health); err != nil {
		t.Fatalf("decode healthz: %v", err)
	}
	if health["status"] != "ok" {
		t.Fatalf("healthz = %v, want a live process to report ok", health)
	}
	if getReadiness(t, server).Ready {
		t.Fatal("readiness agreed with liveness on a robot that cannot be used")
	}
}
