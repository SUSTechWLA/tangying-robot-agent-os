package agentruntime

import (
	"fmt"
	"sort"
)

// The recovery catalog: what a recovery plan is allowed to be made of.
//
// This is the load-bearing safety boundary of model-driven recovery. A model may
// reason about an unfamiliar failure and propose a novel sequence, which is the
// point of using one — but every step it proposes must name an entry here. It
// cannot invent an action, and it cannot reach an interface that is not listed.
//
// The catalog is derived from what the robot already declares (its service
// catalogue, `ListServices`), not from a wish list. An action the robot does not
// expose cannot be performed by proposing it.
//
// # Why each entry carries RequiresApproval
//
// The approval flag is a property of the ACTION, not of the agent that proposed
// it. That is deliberate: as more proposers appear (a model, a pattern library, a
// future policy engine), each one would otherwise need its own notion of what is
// safe, and they would drift. Here the answer is written once, next to the
// action, and every proposer inherits it.

// RiskClass is how much an action is allowed to do on its own.
type RiskClass string

const (
	// RiskReadOnly changes nothing. It may run without asking.
	RiskReadOnly RiskClass = "read_only"
	// RiskBoundedWrite changes the robot or its state in a way the system can
	// undo or re-establish. It requires approval: the approval is what makes
	// "the system decided" into "a person agreed".
	RiskBoundedWrite RiskClass = "bounded_write"
	// RiskNeverAutomatic is never executed by the system at all, by anyone's
	// proposal. It exists so the refusal is a named entry with a reason rather
	// than a silent absence.
	RiskNeverAutomatic RiskClass = "never_automatic"
)

// RecoveryAction is one thing a recovery plan may do.
type RecoveryAction struct {
	// ID is the stable identifier used in proposals, events and reports.
	ID string `json:"id"`
	// Summary says what it does, in one line, for an operator reading a plan.
	Summary string `json:"summary"`
	// Risk decides whether it needs approval.
	Risk RiskClass `json:"risk"`
	// Service is the robot service or internal operation this maps to, so a
	// reader can trace the proposal to the thing that will run.
	//
	// It is written for a person: it may name a sequence, a mode, or a thing that
	// is not a service at all. `Tools` is the machine-readable form, and the two
	// are kept apart rather than one being parsed into the other — a field that
	// has to be read as prose by code is a field that will be read wrongly.
	Service string `json:"service,omitempty"`
	// Tools are the concrete tools this action may call, by the names they are
	// registered under.
	//
	// # Why this is data and not a switch
	//
	// Executing an approved action means mapping it onto real calls, and the
	// obvious way is a `switch` over the thirteen action ids. That would be a
	// hardcoded orchestration table: adding an action would mean editing the
	// executor, and the mapping would live far from the action it describes.
	//
	// Declaring it here instead has three effects. The mapping sits next to the
	// action, so adding one is a data change reviewed with the action. It doubles
	// as the execution scope: an approved action may call these tools and no
	// others, which is the bound the approval was given against. And it is
	// checkable — a test can assert that every action names tools, and that none
	// of them names a never-automatic one.
	//
	// The names are resolved against what the deployment actually offers (the
	// robot's registered services plus this agent's own operations). An action
	// whose tools are not present is not executable, and saying so is better than
	// attempting something that cannot work.
	Tools []string `json:"tools,omitempty"`
	// Shapes are the failure codes or conditions this action is known to help
	// with. It is advice for the proposer, never a permission: an action may be
	// proposed outside its shapes, and an action may not run outside its risk.
	Shapes []string `json:"shapes,omitempty"`
	// Refusal explains why a never-automatic action is refused. Empty for the
	// others.
	Refusal string `json:"refusal,omitempty"`
}

// RecoveryCatalog is an ordered set of actions.
type RecoveryCatalog struct {
	actions []RecoveryAction
	byID    map[string]RecoveryAction
}

// DefaultRecoveryCatalog is what the reference robot can actually do.
//
// Every read-only entry maps to a service the robot declares today; the write
// entries map to the mapping and calibration services. The never-automatic
// entries are listed even though the robot exposes no interface for them — the
// reference runtime has an emergency stop but no release RPC — because naming
// the refusal is what keeps a future proposer from assuming the absence was an
// oversight.
func DefaultRecoveryCatalog() *RecoveryCatalog {
	catalog := &RecoveryCatalog{byID: map[string]RecoveryAction{}}
	for _, action := range []RecoveryAction{
		// --- read-only: establish what is actually true -----------------------
		{
			ID: "observe.re-read", Summary: "重新取一次机器人观测与遥测", Risk: RiskReadOnly,
			Service: "telemetry snapshot",
			Tools:   []string{"telemetry.read"},
			Shapes:  []string{"ANOMALY_TELEMETRY_STALE", "ANOMALY_UNVERIFIED_MUTATION"},
		},
		{
			ID: "nav.read-map", Summary: "读取当前导航地图与定位状态", Risk: RiskReadOnly,
			Service: "navigation.map",
			Tools:   []string{"navigation.map"},
			Shapes:  []string{"NAV_MAP_NOT_READY", "NAV_LOCALIZATION_UNAVAILABLE"},
		},
		{
			ID: "map.read-conflicts", Summary: "把最近采集的点与在用地图比对，报告冲突格子", Risk: RiskReadOnly,
			Service: "mapping.conflicts",
			Tools:   []string{"mapping.conflicts"},
			Shapes:  []string{"NAV_MODEL_COLLISION", "NO_KNOWN_PATH", "GOAL_NOT_CLEAR"},
		},
		{
			ID: "map.read-status", Summary: "读取扫描进度与当前地图", Risk: RiskReadOnly,
			Service: "mapping.status",
			Tools:   []string{"mapping.status"},
			Shapes:  []string{"NAV_MAP_NOT_READY", "SURVEY_UNAVAILABLE"},
		},
		{
			ID: "calibration.read", Summary: "读取机器人标定与模板", Risk: RiskReadOnly,
			Service: "calibration.get",
			Tools:   []string{"calibration.get"},
			Shapes:  []string{"CALIBRATION_CHANGED", "WORKCELL_CALIBRATION_MISMATCH"},
		},
		{
			ID: "execution.read-history", Summary: "读取该任务的执行记录，确认实际走到哪一步", Risk: RiskReadOnly,
			Service: "step_runs",
			Tools:   []string{"execution.read-history"},
			Shapes:  []string{"ANOMALY_ABNORMAL_TASK", "ANOMALY_ACTION_FAILED"},
		},

		// --- bounded writes: real recovery, each needing a person's consent ----
		{
			ID: "map.re-survey", Summary: "重新巡检扫描，产出一张新地图", Risk: RiskBoundedWrite,
			Service: "mapping.start ... mapping.finish",
			Tools:   []string{"mapping.start", "mapping.move", "mapping.finish", "mapping.stop_motion", "mapping.cancel"},
			Shapes:  []string{"NAV_MAP_NOT_READY", "NO_KNOWN_PATH", "STALE_CAPTURE"},
		},
		{
			ID: "map.activate", Summary: "验证并加载一张已保存的地图", Risk: RiskBoundedWrite,
			Service: "mapping.activate",
			Tools:   []string{"mapping.activate"},
			Shapes:  []string{"NAV_MAP_NOT_READY", "NAV_MAP_STALE"},
		},
		{
			ID: "nav.re-localize", Summary: "重新定位：用当前观测重建机器人在地图中的位姿", Risk: RiskBoundedWrite,
			Service: "mapping.move (localization mode)",
			Tools:   []string{"mapping.move", "navigation.map"},
			Shapes:  []string{"NAV_LOCALIZATION_UNAVAILABLE", "NAV_POSE_INVALID"},
		},
		{
			ID: "arm.home", Summary: "机械臂回零，退出未知姿态", Risk: RiskBoundedWrite,
			Service: "recover_to_safe_pose",
			Tools:   []string{"recover_to_safe_pose"},
			Shapes:  []string{"PRE_POSITION_UNAVAILABLE", "NAV_STOW_CONTACT", "GRASP_FAILED"},
		},
		{
			ID: "device.reconnect", Summary: "重连机器人运行时或设备", Risk: RiskBoundedWrite,
			Service: "runtime reconnect",
			Tools:   []string{"runtime.reconnect"},
			Shapes:  []string{"CONNECTION_REFUSED", "RPC_UNAVAILABLE", "TRANSPORT_ERROR"},
		},
		{
			ID: "calibration.run", Summary: "运行机器人注册的标定算法", Risk: RiskBoundedWrite,
			Service: "calibration.run",
			Tools:   []string{"calibration.run"},
			Shapes:  []string{"WORKCELL_CALIBRATION_MISMATCH", "CALIBRATION_REQUIRED"},
		},
		{
			ID: "task.retry-step", Summary: "在重新观测之后重做当前步骤（不是原样重放）", Risk: RiskBoundedWrite,
			Service: "task resume",
			Tools:   []string{"task.resume"},
			Shapes:  []string{"TRANSIENT", "PERCEPTION"},
		},

		// --- never automatic: the system names the refusal -------------------
		{
			ID: "estop.release", Summary: "复位急停", Risk: RiskNeverAutomatic,
			Refusal: "复位急停要人确认现场安全；运行时不提供解除急停的接口，软件在物理上做不到",
		},
		{
			ID: "hardware.replug", Summary: "插拔线缆或更换部件", Risk: RiskNeverAutomatic,
			Refusal: "涉及物理接触与人身安全，只能由人到现场处理",
		},
		{
			ID: "calibration.change", Summary: "修改标定参数", Risk: RiskNeverAutomatic,
			Refusal: "改标定会改变机器人的物理基准，改错会让所有后续动作都偏；必须由人复核后应用",
		},
	} {
		catalog.actions = append(catalog.actions, action)
		catalog.byID[action.ID] = action
	}
	return catalog
}

// NewRecoveryCatalog builds a catalog from an explicit list. It is the
// constructor tests and alternative deployments use.
func NewRecoveryCatalog(actions ...RecoveryAction) (*RecoveryCatalog, error) {
	catalog := &RecoveryCatalog{byID: map[string]RecoveryAction{}}
	for _, action := range actions {
		if action.ID == "" {
			return nil, fmt.Errorf("recovery action requires an id")
		}
		if _, exists := catalog.byID[action.ID]; exists {
			return nil, fmt.Errorf("recovery action %s declared twice", action.ID)
		}
		if action.Risk == "" {
			return nil, fmt.Errorf("recovery action %s must declare a risk class", action.ID)
		}
		if action.Risk == RiskNeverAutomatic && action.Refusal == "" {
			return nil, fmt.Errorf("recovery action %s is never automatic, so it must say why", action.ID)
		}
		catalog.actions = append(catalog.actions, action)
		catalog.byID[action.ID] = action
	}
	return catalog, nil
}

// Lookup returns one action.
func (c *RecoveryCatalog) Lookup(id string) (RecoveryAction, bool) {
	if c == nil {
		return RecoveryAction{}, false
	}
	action, ok := c.byID[id]
	return action, ok
}

// Actions returns every entry, in declaration order.
func (c *RecoveryCatalog) Actions() []RecoveryAction {
	if c == nil {
		return nil
	}
	return append([]RecoveryAction(nil), c.actions...)
}

// Proposable returns the entries a plan may contain: everything except the ones
// that are never automatic.
//
// The never-automatic entries stay in the catalog so that they can be shown and
// refused by name. A plan may reference one, and it will be refused with its
// stated reason — which is a better answer for a reader than "unknown action".
func (c *RecoveryCatalog) Proposable() []RecoveryAction {
	proposable := make([]RecoveryAction, 0, len(c.actions))
	for _, action := range c.actions {
		if action.Risk == RiskNeverAutomatic {
			continue
		}
		proposable = append(proposable, action)
	}
	return proposable
}

// ForShapes returns the read-only entries that are known to help with a failure
// shape, sorted by id so a report is reproducible.
//
// It is used by the deterministic route: when a fault declares nothing, the
// safest useful thing is to go and look, and the catalog already names every
// read-only way to do that.
func (c *RecoveryCatalog) ForShapes(shape string) []RecoveryAction {
	if c == nil || shape == "" {
		return nil
	}
	matched := make([]RecoveryAction, 0)
	for _, action := range c.actions {
		if action.Risk != RiskReadOnly {
			continue
		}
		for _, candidate := range action.Shapes {
			if candidate == shape {
				matched = append(matched, action)
				break
			}
		}
	}
	sort.SliceStable(matched, func(i, j int) bool { return matched[i].ID < matched[j].ID })
	return matched
}

// Encode renders the catalog for a report or an event payload, so an operator
// can see what the system was allowed to choose from. A plan is only reviewable
// against the options that existed when it was made.
func (c *RecoveryCatalog) Encode() map[string]any {
	entries := make([]any, 0, len(c.actions))
	for _, action := range c.actions {
		entry := map[string]any{
			"id": action.ID, "summary": action.Summary,
			"risk": string(action.Risk), "requiresApproval": action.RequiresApproval(),
		}
		if action.Service != "" {
			entry["service"] = action.Service
		}
		if len(action.Shapes) > 0 {
			entry["shapes"] = append([]string(nil), action.Shapes...)
		}
		if action.Refusal != "" {
			entry["refusal"] = action.Refusal
		}
		entries = append(entries, entry)
	}
	return map[string]any{"actionCount": len(c.actions), "actions": entries}
}

// RequiresApproval reports whether this action needs a person's consent.
//
// Read-only actions do not. Everything else does, including the never-automatic
// ones — which are refused before approval is even considered, so a reader never
// has to work out which of the two gates applies.
func (a RecoveryAction) RequiresApproval() bool {
	return a.Risk != RiskReadOnly
}

// Executable reports whether the system may run this action at all, given that a
// person has already approved it when approval is required.
//
// The comparison is against the two risks that permit execution rather than
// against the one that forbids it. Written the other way round it says yes to an
// action whose risk nobody declared — an empty string is "not never-automatic" —
// so an entry added without a risk class would be silently executable, and the
// one field that decides whether a person is asked would default to "not asked".
func (a RecoveryAction) Executable() bool {
	return a.Risk == RiskReadOnly || a.Risk == RiskBoundedWrite
}
