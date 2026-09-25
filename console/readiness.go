package console

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Readiness: the answer to "can I use this robot right now".
//
// The console had no such answer. `GET /healthz` returned a constant
// `{"status":"ok"}`, and `GET /v1/runtime` returned a snapshot or a connection
// error — both of which are about the process and the socket, not about whether
// the robot can do anything. A robot with a latched emergency stop, no active
// map, and a physical step whose outcome is unknown reported `status: ok`, and an
// owner reading that would reasonably conclude the robot was fine.
//
// # Why this is a separate endpoint instead of a stricter /healthz
//
// They answer different questions and a machine acts on each differently.
// Liveness asks "should I restart this process" — a robot in an emergency stop is
// a healthy process doing its job, and restarting it would be an outage caused by
// a working safety feature. Readiness asks "should I offer this to a person" —
// and being stopped is precisely a reason not to.
//
// Folding them together is how a container ends up in a restart loop because its
// robot is safely stopped. So /healthz stays liveness and keeps returning ok, and
// this endpoint is what the console and the owner read.

// ReadinessState is how one prerequisite is doing.
type ReadinessState string

const (
	// ReadinessReady means the check passed on evidence.
	ReadinessReady ReadinessState = "ready"
	// ReadinessAction means something a person can fix is in the way.
	ReadinessAction ReadinessState = "action"
	// ReadinessUnknown means the check could not be made.
	//
	// It is deliberately not a kind of ready. "I could not tell" and "this is
	// fine" are different answers, and reporting the first as the second is the
	// mistake this whole repository keeps refusing to make.
	ReadinessUnknown ReadinessState = "unknown"
)

// ReadinessCheck is one prerequisite, with what to do about it.
type ReadinessCheck struct {
	// ID is stable, so a console can address one check.
	ID string `json:"id"`
	// Title names the prerequisite in an owner's words.
	Title string `json:"title"`
	// State is ready, action or unknown.
	State ReadinessState `json:"state"`
	// Situation says what is true, in one sentence.
	Situation string `json:"situation"`
	// Action says what a person should do. Empty when nothing is needed.
	Action string `json:"action,omitempty"`
	// Detail is the underlying evidence: a fault code, an error, a count. It is
	// what a support conversation needs and what an owner should not have to read
	// first.
	Detail string `json:"detail,omitempty"`
	// Blocking says whether this check alone is enough to make the robot unusable.
	Blocking bool `json:"blocking"`
}

// ReadinessReport is the whole answer.
type ReadinessReport struct {
	// Ready is true only when every blocking check passed on evidence.
	Ready bool `json:"ready"`
	// Summary is the one sentence an owner reads if they read nothing else.
	Summary string `json:"summary"`
	// NextID is the check to deal with first, empty when ready.
	NextID string `json:"nextId,omitempty"`
	// Checks are ordered: what blocks first, then what is merely unknown, then
	// what passed, so the first row is always the useful one.
	Checks []ReadinessCheck `json:"checks"`
	// CheckedAt is when this was computed.
	CheckedAt time.Time `json:"checkedAt"`
	// UnresolvedOutcome counts tasks whose physical result is unknown. It is a
	// field of its own because it is the one condition that forbids retrying, and
	// a reader should not have to find it in a list.
	UnresolvedOutcome int `json:"unresolvedOutcome"`
	// Language names the natural-language capability that is actually in effect.
	Language LanguageReadiness `json:"language"`
}

// LanguageReadiness says how much of a sentence the system can currently
// understand.
//
// It is part of readiness rather than a settings detail because it decides
// whether "just talk to it" works at all. With no model configured the system
// understands a fixed vocabulary of objects, colours and containers and nothing
// else, and an owner who was not told that would conclude the robot is stupid
// rather than unconfigured.
type LanguageReadiness struct {
	// Provider is the configured provider name, for example "deterministic".
	Provider string `json:"provider"`
	// Model is the configured model, when there is one.
	Model string `json:"model,omitempty"`
	// ModelConfigured says whether a model will actually be used.
	ModelConfigured bool `json:"modelConfigured"`
	// Vocabulary is what is understood without a model, for display.
	Vocabulary string `json:"vocabulary,omitempty"`
	// Note explains the consequence in one sentence.
	Note string `json:"note"`
}

// readinessSignals is everything the report is computed from.
//
// It is a struct rather than a set of arguments so a test can vary one signal and
// see exactly one row change, which is the difference between testing the report
// and testing the wiring.
type readinessSignals struct {
	// robotReachable is nil when the runtime was not asked or the answer was an
	// error, and non-nil when the robot answered.
	robotReachable *bool
	robotDetail    string
	// blockingFaults are the robot's own blocking faults, with its instructions.
	blockingFaults []faultSignal
	emergencyStop  *bool
	// mapReady is nil when no map status has been read.
	mapReady     *bool
	mapDetail    string
	supervision  tasks.SupervisionStatus
	settings     *ConfigStatus
	unresolved   int
	unresolvedAt []string
	now          time.Time
}

type faultSignal struct {
	Code        string
	Module      string
	Instruction string
}

// buildReadiness turns the signals into the ordered report.
//
// It is a pure function of its input. Every judgement about what matters lives
// here rather than in the handler, so the ordering and the wording can be tested
// without a robot, a network or a clock.
func buildReadiness(signals readinessSignals) ReadinessReport {
	checks := []ReadinessCheck{
		supervisionCheck(signals),
		robotLinkCheck(signals),
		safetyCheck(signals),
		faultCheck(signals),
		mapCheck(signals),
		reconciliationCheck(signals),
	}
	report := ReadinessReport{
		Checks:            orderReadiness(checks),
		CheckedAt:         signals.now,
		UnresolvedOutcome: signals.unresolved,
		Language:          languageReadiness(signals.settings),
	}
	for _, check := range report.Checks {
		if check.State == ReadinessAction && check.Blocking {
			report.NextID = check.ID
			break
		}
		if report.NextID == "" && check.State == ReadinessUnknown && check.Blocking {
			report.NextID = check.ID
		}
	}
	report.Ready = report.NextID == ""
	report.Summary = readinessSummary(report)
	return report
}

// orderReadiness sorts blocking problems first, then unknowns, then passes.
//
// Stable within a group so the list does not reshuffle between two reads of the
// same state, which would make it look as though something had changed.
func orderReadiness(checks []ReadinessCheck) []ReadinessCheck {
	rank := func(check ReadinessCheck) int {
		switch {
		case check.State == ReadinessAction && check.Blocking:
			return 0
		case check.State == ReadinessAction:
			return 1
		case check.State == ReadinessUnknown && check.Blocking:
			return 2
		case check.State == ReadinessUnknown:
			return 3
		default:
			return 4
		}
	}
	ordered := append([]ReadinessCheck(nil), checks...)
	for index := 1; index < len(ordered); index++ {
		for position := index; position > 0 && rank(ordered[position]) < rank(ordered[position-1]); position-- {
			ordered[position], ordered[position-1] = ordered[position-1], ordered[position]
		}
	}
	return ordered
}

func readinessSummary(report ReadinessReport) string {
	if report.Ready {
		return "机器人可以用了。"
	}
	for _, check := range report.Checks {
		if check.ID != report.NextID {
			continue
		}
		if check.State == ReadinessUnknown {
			return "还不能确定机器人能不能用：" + check.Situation
		}
		return "机器人还不能用：" + check.Situation
	}
	return "机器人还不能用。"
}

// supervisionCheck reports whether anything is watching for problems.
//
// It is blocking because an unwatched robot is not a usable robot in the sense
// this product promises: the whole self-diagnosis story is void if the agent that
// does the diagnosing is switched off, and nothing else in the system would say
// so.
func supervisionCheck(signals readinessSignals) ReadinessCheck {
	check := ReadinessCheck{
		ID: "supervision", Title: "问题发现", Blocking: true,
	}
	if signals.supervision.Enabled && signals.supervision.Observing {
		check.State = ReadinessState("ready")
		check.Situation = "有监督 agent 在盯着异常。"
		return check
	}
	check.State = ReadinessAction
	check.Situation = "没有监督 agent 在运行，机器人出问题不会被告警。"
	check.Action = "用 TANGYING_AGENTS 启用 ops 与 recovery 后重启 Local Agent。"
	check.Detail = signals.supervision.Reason
	return check
}

func robotLinkCheck(signals readinessSignals) ReadinessCheck {
	check := ReadinessCheck{ID: "robot", Title: "连接机器人", Blocking: true}
	switch {
	case signals.robotReachable == nil:
		check.State = ReadinessUnknown
		check.Situation = "还没有读到机器人的状态。"
		check.Action = "确认机器人已通电、和这台电脑在同一网络，然后刷新。"
	case *signals.robotReachable:
		check.State = ReadinessState("ready")
		check.Situation = "机器人已连接。"
	default:
		check.State = ReadinessAction
		check.Situation = "连不上机器人。"
		check.Action = "检查电源与网络；如果这台机器人还没配对，先在控制台里完成配对。"
		check.Detail = signals.robotDetail
	}
	return check
}

func safetyCheck(signals readinessSignals) ReadinessCheck {
	check := ReadinessCheck{ID: "safety", Title: "急停", Blocking: true}
	switch {
	case signals.emergencyStop == nil:
		check.State = ReadinessUnknown
		check.Situation = "还没有读到急停状态。"
		check.Action = "等连接稳定后刷新；连接不稳定时不要下指令。"
	case *signals.emergencyStop:
		check.State = ReadinessAction
		check.Situation = "机器人处于急停，不会执行任何动作。"
		// No software path may release it, so the instruction names the physical
		// act rather than offering a button that does not exist.
		check.Action = "确认现场安全后，按机器人本体上的复位步骤解除急停。软件不会替你解除。"
	default:
		check.State = ReadinessState("ready")
		check.Situation = "没有急停。"
	}
	return check
}

// faultCheck reports the robot's own account of what is broken.
//
// The sentences come from the robot rather than being written here. The robot is
// the only thing that knows what its fault means for its own hardware, and the
// operator instruction it publishes is the same sentence the console, the model
// and the incident record all quote.
func faultCheck(signals readinessSignals) ReadinessCheck {
	check := ReadinessCheck{ID: "faults", Title: "机器人自检", Blocking: true}
	if len(signals.blockingFaults) == 0 {
		check.State = ReadinessState("ready")
		check.Situation = "机器人没有报告阻塞性故障。"
		return check
	}
	check.State = ReadinessAction
	fault := signals.blockingFaults[0]
	check.Situation = "机器人报告了故障，相关能力已停用。"
	check.Detail = fault.Code
	if fault.Module != "" {
		check.Detail = fault.Module + ":" + fault.Code
	}
	// The robot's own operator instruction is preferred, because it is the one
	// sentence the console, the model and the incident record all quote, and a
	// paraphrase here would be a fourth version of the same advice.
	//
	// When it is not available the fallback is a next step rather than advice:
	// it does not pretend to know what the fault means, it says who to tell.
	check.Action = fault.Instruction
	if check.Action == "" {
		check.Action = "记下上面的故障码，把机器人本体服务的日志一起发给支持人员。"
	}
	return check
}

func mapCheck(signals readinessSignals) ReadinessCheck {
	check := ReadinessCheck{ID: "map", Title: "场景地图", Blocking: true}
	switch {
	case signals.mapReady == nil:
		check.State = ReadinessUnknown
		check.Situation = "还没有读到地图状态。"
		check.Action = "等连接稳定后刷新。"
	case *signals.mapReady:
		check.State = ReadinessState("ready")
		check.Situation = "地图已启用，可以跨房间执行任务。"
	default:
		check.State = ReadinessAction
		check.Situation = "还没有启用的地图，需要移动的任务会失败。"
		check.Action = "让机器人完成一次巡检建图，然后在控制台启用地图。"
		check.Detail = signals.mapDetail
	}
	return check
}

// reconciliationCheck is the one condition that forbids retrying.
//
// It is blocking and it is phrased as an instruction to go and look, because the
// only way to resolve it is for a person to observe the world. No software path
// may clear it.
func reconciliationCheck(signals readinessSignals) ReadinessCheck {
	check := ReadinessCheck{ID: "reconciliation", Title: "动作结果", Blocking: true}
	if signals.unresolved == 0 {
		check.State = ReadinessState("ready")
		check.Situation = "没有结果未知的动作。"
		return check
	}
	check.State = ReadinessAction
	check.Situation = "有动作的结果没能确认，系统不会自动重试。"
	check.Action = "看一眼机器人现在的位置和物品在哪，确认实际发生了什么，再决定是否继续。"
	check.Detail = strings.Join(signals.unresolvedAt, ", ")
	return check
}

// defaultVocabulary is what the deterministic parser accepts, in one line.
//
// It is written out rather than derived from the parser's regexes: the point is
// to tell somebody what they can say, and a generated list of patterns would be
// both unreadable and a second implementation of the grammar.
const defaultVocabulary = "颜色（红/蓝/绿）+ 物品（杯子/水瓶/方块）+ 位置（收纳盒/托盘/交接区），中英文皆可"

// languageReadiness describes how much of a sentence is understood.
//
// Non-blocking: the fixed vocabulary works, and a robot that understands a
// hundred sentences is usable. But it must be visible, because "the robot did not
// understand me" and "the robot has no model configured" look identical from the
// outside and only one of them is a bug.
func languageReadiness(settings *ConfigStatus) LanguageReadiness {
	if settings == nil {
		// No settings store is not the same as no model, but the effect on what a
		// user can say is the same, and the vocabulary is what they need to know.
		return LanguageReadiness{
			Provider: "unknown", ModelConfigured: false,
			Vocabulary: defaultVocabulary,
			Note:       "读不到语言配置；当前只认固定词表。",
		}
	}
	readiness := LanguageReadiness{Provider: settings.Provider, Model: settings.Model}
	baseURL := settings.BaseURL
	if intent, ok := settings.Stages["intent"]; ok {
		readiness.Provider, readiness.Model, baseURL = intent.Provider, intent.Model, intent.BaseURL
	}
	readiness.ModelConfigured = readiness.Provider != "deterministic" &&
		baseURL != "" && readiness.Model != ""
	if readiness.ModelConfigured {
		readiness.Note = "已配置模型，可以说日常说法。"
		return readiness
	}
	readiness.Vocabulary = defaultVocabulary
	readiness.Note = "还没有配置模型，只认固定词表；超出词表的说法会被拒绝。"
	return readiness
}

// readiness collects the signals and answers.
func (s *Server) readiness(w http.ResponseWriter, r *http.Request) {
	report := buildReadiness(s.readinessSignals(r.Context()))
	writeJSON(w, http.StatusOK, report)
}

func (s *Server) readinessSignals(ctx context.Context) readinessSignals {
	signals := readinessSignals{now: time.Now().UTC()}

	if s.runtime != nil {
		snapshot, err := s.runtime.Info(ctx)
		reachable := err == nil
		signals.robotReachable = &reachable
		if err != nil {
			signals.robotDetail = err.Error()
		} else {
			signals.applyRobotSnapshot(snapshot)
		}
	}
	// The robot's own fault report carries the operator instruction that the rest
	// of the system quotes. It is asked for separately from the runtime snapshot
	// because the snapshot lists what is blocked and this says what to do about
	// it, and only one of the two is a sentence a person can act on.
	if s.camera != nil && signals.robotReachable != nil && *signals.robotReachable {
		fetchContext, cancel := context.WithTimeout(ctx, 3*time.Second)
		defer cancel()
		if snapshot, err := s.camera.TelemetrySource(fetchContext, "", ""); err == nil && snapshot.Faults != nil {
			signals.blockingFaults = faultSignalsFromReport(*snapshot.Faults)
		}
	}
	if s.settings != nil {
		status := s.settings.Status()
		signals.settings = &status
	}
	if reporter, ok := s.executor.(supervisionReporter); ok {
		signals.supervision = reporter.SupervisionStatus()
	}
	signals.unresolvedAt, signals.unresolved = s.unresolvedOutcomes(ctx)
	return signals
}

// applyRobotSnapshot reads the robot's own readiness report.
//
// Everything here comes from what the robot says about itself, not from a
// re-derivation on this side. The robot publishes `Ready`, a list of blockers, and
// per-capability availability with the fault that removed it; guessing at those
// from the outside would create a second opinion about the robot's own hardware,
// and the two would disagree exactly when it mattered.
func (s *readinessSignals) applyRobotSnapshot(snapshot runtime.Snapshot) {
	emergency := false
	for _, blocker := range snapshot.Blockers {
		fault := parseBlocker(blocker)
		if strings.Contains(blocker, "EMERGENCY_STOP") {
			emergency = true
			continue
		}
		s.blockingFaults = append(s.blockingFaults, fault)
	}
	// A capability that is unavailable names the fault that removed it, so a
	// blocker that is not in the headline list is still found here.
	navigationKnown := false
	for _, capability := range snapshot.Capabilities {
		for _, blocker := range capability.Blockers {
			if strings.Contains(blocker, "EMERGENCY_STOP") {
				emergency = true
			}
			if strings.Contains(blocker, "NAV_MAP_NOT_READY") {
				navigationKnown = true
				notReady := false
				s.mapReady = &notReady
				s.mapDetail = blocker
			}
			if !containsFault(s.blockingFaults, blocker) && !strings.Contains(blocker, "EMERGENCY_STOP") {
				s.blockingFaults = append(s.blockingFaults, parseBlocker(blocker))
			}
		}
		if capability.Name == "navigation.navigate" && capability.Available {
			navigationKnown = true
			ready := true
			s.mapReady = &ready
		}
	}
	if !navigationKnown {
		// No opinion either way. Reporting "no map" here would be inventing a
		// fault, and reporting "map fine" would be inventing a check.
		s.mapReady = nil
	}
	emergencyValue := emergency
	s.emergencyStop = &emergencyValue
}

// parseBlocker splits the robot's `module:CODE` blocker form.
//
// The instruction is left empty rather than filled with a sentence written here:
// the robot's own operator instruction is the one the console, the model and the
// incident record all quote, and a paraphrase on this side would be a fourth
// version of the same advice.
func parseBlocker(blocker string) faultSignal {
	module, code, found := strings.Cut(blocker, ":")
	if !found {
		return faultSignal{Code: blocker}
	}
	return faultSignal{Module: module, Code: code}
}

// faultSignalsFromReport reads the robot's blocking faults, with its sentences.
func faultSignalsFromReport(report robotcontract.FaultReport) []faultSignal {
	blocking := report.Blocking()
	signals := make([]faultSignal, 0, len(blocking))
	for _, fault := range blocking {
		signals = append(signals, faultSignal{
			Code: fault.Code, Module: fault.ModuleID, Instruction: fault.UserInstruction,
		})
	}
	return signals
}

func containsFault(faults []faultSignal, blocker string) bool {
	for _, fault := range faults {
		if fault.Module+":"+fault.Code == blocker || fault.Code == blocker {
			return true
		}
	}
	return false
}

func (s *Server) unresolvedOutcomes(ctx context.Context) ([]string, int) {
	if s.service == nil {
		return nil, 0
	}
	all, err := s.service.List(ctx)
	if err != nil {
		return nil, 0
	}
	ids := make([]string, 0)
	for _, task := range all {
		if task == nil {
			continue
		}
		// A task that has not reached a successful end and has a physical step
		// left unconfirmed is exactly the state the closed-loop contract forbids
		// retrying.
		if task.State != taskgraph.StateSucceeded && s.taskNeedsReconciliation(ctx, task.ID) {
			ids = append(ids, task.ID)
		}
	}
	return ids, len(ids)
}

// taskNeedsReconciliation asks the executor, which is the only thing that can
// answer: the question lives in the execution records, not on the task row.
//
// An executor that cannot answer leaves the task out rather than counting it. The
// alert banner deliberately errs the other way — an unresolved warning is safer
// than a hidden one — but a readiness report that listed every task it could not
// check would be unusable, and "unknown" is already its own state elsewhere in
// this report.
func (s *Server) taskNeedsReconciliation(ctx context.Context, taskID string) bool {
	executor, ok := s.executor.(recoveryExecutor)
	if !ok {
		return false
	}
	view, err := executor.Recovery(ctx, taskID)
	if err != nil {
		return false
	}
	return view.RequiresReconciliation
}
