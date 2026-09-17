package agentruntime

import (
	"sort"
	"strings"
	"time"
)

// The audit trail behind every recovery decision.
//
// A conclusion is only reviewable if the road to it is recorded. Reasoning in
// prose is not reviewable: a reader cannot tell which claim came from a
// database, which came from a model's memory, and which was assumed. So every
// step of an investigation is a record with a stable shape, and the records are
// published with the conclusion.
//
// This is the difference the requirement asks for: not "the agent says the map
// is stale" but "the agent read `observation_evidence` for task X, found the
// newest row is 40 minutes old, and concluded the map is stale from that".
//
// The shapes here are deliberately generic. A database read and a model call are
// both "a step that produced findings", because from the reader's side they are
// the same kind of thing: something was done, and something came back.

// TrailKind is what a step did.
type TrailKind string

const (
	// TrailQuery is a read of a data store.
	TrailQuery TrailKind = "query"
	// TrailRule is a deterministic rule that ran over what was read.
	TrailRule TrailKind = "rule"
	// TrailModel is a model call.
	TrailModel TrailKind = "model"
	// TrailDecision is the agent choosing what to do with what it found.
	TrailDecision TrailKind = "decision"
	// TrailAction is an action being submitted for execution.
	TrailAction TrailKind = "action"
	// TrailVerification is the re-check that decides whether an action worked.
	TrailVerification TrailKind = "verification"
)

// DataSource names where a query read from, precisely enough that a reader can
// go and look at the same place.
//
// It carries the table or file name rather than a slogan like "the task store":
// the requirement is that an operator can see which database and which table
// was consulted, and "the task store" does not answer that.
type DataSource struct {
	// Kind is the store's nature: sqlite, memory, file, runtime, eventbus.
	Kind string `json:"kind"`
	// Name is the store's identity, for example the database path or the
	// directory a file was read from.
	Name string `json:"name,omitempty"`
	// Table is the table, collection or file name within the store.
	Table string `json:"table,omitempty"`
	// Query is the selection actually applied, when there is one. It is written
	// as a human-readable predicate rather than raw SQL: a reader needs to know
	// "rows for this task, newest first", not the exact statement.
	Query string `json:"query,omitempty"`
}

// String renders a source the way it should appear in a report line.
func (s DataSource) String() string {
	parts := make([]string, 0, 3)
	if s.Kind != "" {
		parts = append(parts, s.Kind)
	}
	if s.Name != "" {
		parts = append(parts, s.Name)
	}
	if s.Table != "" {
		parts = append(parts, s.Table)
	}
	label := strings.Join(parts, ":")
	if s.Query != "" {
		label += " (" + s.Query + ")"
	}
	return label
}

// TrailStep is one recorded step of an investigation.
type TrailStep struct {
	// Sequence orders steps within one investigation, starting at 1.
	Sequence int       `json:"sequence"`
	Kind     TrailKind `json:"kind"`
	// Name is the tool, rule or model that ran. It is what a reader would look
	// up to understand the step.
	Name string `json:"name"`
	// Source is set for queries.
	Source DataSource `json:"source,omitempty"`
	// Summary says what this step did, in one line.
	Summary string `json:"summary"`
	// Findings are what came back, as bounded scalars. Values must be printable:
	// a step whose result cannot be shown cannot be reviewed.
	Findings map[string]any `json:"findings,omitempty"`
	// Rows is how many records the step examined, when it read a store. It is
	// separate from Findings because "I read 200 rows" and "I read 0 rows" are
	// different situations that a summary alone tends to blur.
	Rows int `json:"rows,omitempty"`
	// Error records a step that failed. An investigation that hid its failed
	// steps would look more certain than it was.
	Error string `json:"error,omitempty"`
	// At is when the step ran.
	At time.Time `json:"at"`
}

// Trail is one investigation, in order.
type Trail struct {
	// ID identifies the investigation, so a proposal can be traced back to the
	// steps that produced it.
	ID string `json:"id"`
	// TaskID is the task under investigation, when there is one.
	TaskID string `json:"taskId,omitempty"`
	// Trigger names what started the investigation, for example the anomaly code.
	Trigger string      `json:"trigger,omitempty"`
	Steps   []TrailStep `json:"steps"`
	// StartedAt and EndedAt bound the investigation.
	StartedAt time.Time `json:"startedAt"`
	EndedAt   time.Time `json:"endedAt"`
}

// Append records one step, assigning its sequence number. A Trail is written
// once per investigation and never rewritten, so it is safe to publish.
func (t *Trail) Append(step TrailStep) {
	if step.At.IsZero() {
		step.At = time.Now().UTC()
	}
	step.Sequence = len(t.Steps) + 1
	t.Steps = append(t.Steps, step)
}

// Len reports how many steps were recorded.
func (t *Trail) Len() int {
	if t == nil {
		return 0
	}
	return len(t.Steps)
}

// Encode renders the trail for an event payload.
//
// Steps become a list of maps rather than a JSON string so a console can render
// them as rows, and so a reader of the raw event can still see the fields. The
// per-step keys are the same ones the console shows, which is why they are
// written out here explicitly instead of relying on struct tags.
func (t *Trail) Encode() map[string]any {
	if t == nil {
		return nil
	}
	steps := make([]any, 0, len(t.Steps))
	for _, step := range t.Steps {
		entry := map[string]any{
			"sequence": step.Sequence,
			"kind":     string(step.Kind),
			"name":     step.Name,
			"summary":  step.Summary,
			"at":       step.At,
		}
		if step.Source != (DataSource{}) {
			entry["source"] = step.Source.String()
			entry["sourceDetail"] = map[string]any{
				"kind": step.Source.Kind, "name": step.Source.Name,
				"table": step.Source.Table, "query": step.Source.Query,
			}
		}
		if len(step.Findings) > 0 {
			entry["findings"] = step.Findings
		}
		if step.Rows > 0 {
			entry["rows"] = step.Rows
		}
		if step.Error != "" {
			entry["error"] = step.Error
		}
		steps = append(steps, entry)
	}
	payload := map[string]any{
		"trailId": t.ID, "steps": steps,
		"stepCount": len(t.Steps), "startedAt": t.StartedAt, "endedAt": t.EndedAt,
	}
	if t.TaskID != "" {
		payload["taskId"] = t.TaskID
	}
	if t.Trigger != "" {
		payload["trigger"] = t.Trigger
	}
	return payload
}

// dataSources lists where an investigation may read, with the real table names.
//
// It is a constant because an operator reading a report needs to be able to
// check the claim against the actual schema, and a name invented at the call
// site would drift. The names match middleware/sqlite's schema.
var dataSources = struct {
	Tasks               DataSource
	TaskEvents          DataSource
	StepRuns            DataSource
	ObservationEvidence DataSource
	IncidentBundles     DataSource
	RuntimeSnapshot     DataSource
	RecoveryCatalog     DataSource
}{
	Tasks:               DataSource{Kind: "sqlite", Table: "tasks"},
	TaskEvents:          DataSource{Kind: "sqlite", Table: "task_events"},
	StepRuns:            DataSource{Kind: "sqlite", Table: "step_runs"},
	ObservationEvidence: DataSource{Kind: "sqlite", Table: "observation_evidence"},
	IncidentBundles:     DataSource{Kind: "file", Table: "incident bundle"},
	RuntimeSnapshot:     DataSource{Kind: "runtime", Table: "telemetry snapshot"},
	RecoveryCatalog:     DataSource{Kind: "memory", Table: "recovery catalog"},
}

// trailID builds an investigation identity from what triggered it, so two
// investigations of the same condition in the same task are recognisable as the
// same line of inquiry rather than unrelated events.
func trailID(taskID, trigger string) string {
	if taskID == "" {
		return "trail-" + trigger
	}
	return "trail-" + taskID + "-" + trigger
}

// sortStrings is a local helper: findings frequently carry lists that must be
// rendered deterministically, because a report whose ordering changes between
// reads cannot be compared with itself.
func sortStrings(values []string) []string {
	copied := append([]string(nil), values...)
	sort.Strings(copied)
	return copied
}
