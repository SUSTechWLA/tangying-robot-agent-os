package agentcontext

import (
	"encoding/json"
	"fmt"
)

const DecisionVersion = "decision-context.v1"

// DecisionContext is a typed envelope around source-owned values. Null means
// unknown, never false. A renderer does not mint facts, approvals, or identities.
type DecisionContext struct {
	SchemaVersion string           `json:"schema_version"`
	Stage         string           `json:"stage"`
	Scope         DecisionScope    `json:"scope"`
	Snapshot      SnapshotIdentity `json:"snapshot"`
	Goal          map[string]any   `json:"goal"`
	Steps         []map[string]any `json:"steps"`
	Records       []DecisionRecord `json:"records"`
	Attempts      []map[string]any `json:"attempts"`
	Tools         []map[string]any `json:"tools"`
	Constraints   []string         `json:"constraints"`
	Missing       []string         `json:"missing"`
}
type DecisionScope struct {
	TaskID    *string `json:"task_id"`
	RobotID   *string `json:"robot_id"`
	Revision  *int    `json:"plan_revision"`
	EpisodeID *string `json:"episode_id"`
}
type SnapshotIdentity struct {
	AsOfMS         *int64 `json:"as_of_ms"`
	ClockDomain    string `json:"clock_domain"`
	LedgerSequence *int64 `json:"ledger_sequence"`
	Complete       bool   `json:"history_complete"`
	OmittedEvents  int    `json:"omitted_events"`
}
type DecisionRecord struct {
	ID            string         `json:"id"`
	Kind          string         `json:"kind"`
	Scope         DecisionScope  `json:"scope"`
	StepID        *string        `json:"step_id"`
	AttemptID     *string        `json:"attempt_id"`
	Source        string         `json:"source"`
	SourceVersion *string        `json:"source_version"`
	ObservedMS    *int64         `json:"observed_ms"`
	ValidUntilMS  *int64         `json:"valid_until_ms"`
	ClockDomain   string         `json:"clock_domain"`
	Payload       map[string]any `json:"payload"`
	EvidenceIDs   []string       `json:"evidence_ids"`
	Supersedes    []string       `json:"supersedes"`
}

func knownString(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
func knownInt(n int) *int {
	if n <= 0 {
		return nil
	}
	return &n
}
func knownTime(n int64) *int64 {
	if n <= 0 {
		return nil
	}
	return &n
}
func decisionScope(s Scope) DecisionScope {
	return DecisionScope{TaskID: knownString(s.TaskID), RobotID: knownString(s.RobotID), Revision: knownInt(s.PlanRevision)}
}

// DecisionFrom preserves legacy source text while exposing unavailable metadata.
// No timestamp or action binding is inferred from the current task identity.
func DecisionFrom(d Document) DecisionContext {
	p := DecisionContext{SchemaVersion: DecisionVersion, Stage: d.Stage, Scope: decisionScope(d.Scope),
		Snapshot: SnapshotIdentity{AsOfMS: knownTime(d.AsOfMS), ClockDomain: "utc", Complete: false},
		Goal:     map[string]any{"request": d.Goal, "summary": d.Summary, "current_step_id": d.CurrentStep, "questions": d.Questions},
		Steps:    []map[string]any{}, Records: []DecisionRecord{}, Attempts: []map[string]any{}, Tools: []map[string]any{}, Constraints: append([]string{}, d.Constraints...), Missing: []string{}}
	if p.Stage == "" {
		p.Stage = StageForRole(d.Role)
	}
	if p.Snapshot.AsOfMS == nil {
		p.Snapshot.ClockDomain = "unknown"
	}
	for _, s := range d.Steps {
		p.Steps = append(p.Steps, map[string]any{"id": s.ID, "action": s.Action, "state": s.State, "depends_on": s.DependsOn, "expected": s.Expected})
	}
	for _, r := range d.Records {
		clock := "unknown"
		if r.ObservedMS > 0 {
			clock = "utc"
		}
		p.Records = append(p.Records, DecisionRecord{ID: r.ID, Kind: r.Kind, Scope: decisionScope(r.Scope), Source: "legacy_projection", ObservedMS: knownTime(r.ObservedMS), ValidUntilMS: knownTime(r.ValidUntilMS), ClockDomain: clock, Payload: map[string]any{"statement": r.Statement}, EvidenceIDs: append([]string{}, r.EvidenceIDs...), Supersedes: append([]string{}, r.Supersedes...)})
	}
	for _, a := range d.Attempts {
		p.Attempts = append(p.Attempts, map[string]any{"id": a.ID, "tool": a.Tool, "arguments": a.Arguments, "verdict": a.Verdict, "detail": a.Detail, "evidence_ids": a.EvidenceIDs, "execution_state": nil, "idempotency_key": nil, "approval_id": nil})
	}
	for _, t := range d.Tools {
		p.Tools = append(p.Tools, map[string]any{"name": t.Name, "description": t.Description, "mutates_world": t.MutatesWorld, "requires_approval": t.RequiresApproval, "argument_schema": nil})
	}
	if native := d.DecisionMetadata; native != nil {
		p.Scope = native.Scope
		p.Snapshot = native.Snapshot
		for k, v := range native.Goal {
			if k != "request" && k != "summary" && k != "current_step_id" && k != "questions" {
				p.Goal[k] = v
			}
		}
		byID := map[string]DecisionRecord{}
		for _, r := range native.Records {
			byID[r.ID] = r
		}
		for i, r := range p.Records {
			if enriched, ok := byID[r.ID]; ok {
				p.Records[i] = enriched
			}
		}
		p.Missing = append(p.Missing, native.Missing...)
	}
	return p
}
func (p DecisionContext) Validate() error {
	if p.SchemaVersion != DecisionVersion || StageForRole(p.Stage) != p.Stage || p.Stage == "unknown" {
		return fmt.Errorf("invalid decision schema or stage")
	}
	if _, err := json.Marshal(p); err != nil {
		return err
	}
	ids := map[string]bool{}
	for _, r := range p.Records {
		if r.ID == "" || ids[r.ID] {
			return fmt.Errorf("duplicate or missing record id")
		}
		ids[r.ID] = true
		if r.ObservedMS != nil && *r.ObservedMS < 0 || r.ValidUntilMS != nil && *r.ValidUntilMS < 0 {
			return fmt.Errorf("negative record time")
		}
	}
	if p.Snapshot.AsOfMS != nil && *p.Snapshot.AsOfMS < 0 || p.Snapshot.OmittedEvents < 0 {
		return fmt.Errorf("invalid snapshot")
	}
	return nil
}
