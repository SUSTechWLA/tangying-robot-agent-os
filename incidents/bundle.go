// Package incidents turns an abnormal task ending into a durable, machine-readable
// bundle so a later diagnosis - by a person, by an AI, or by a coding agent - does
// not have to reconstruct it from five live endpoints that may since have changed.
//
// The split is deliberate: this package records **facts** (state, events, step
// runs, recovery verdict, timing snapshot, revisions). It does not classify,
// speculate, or propose. Classification lives in one place - the fault-family
// table in `scripts/diagnose_task.py` - because a second copy of that knowledge in
// another language is a copy that will drift.
//
// Writing is best effort and bounded: an incident must never fail a task, and a
// robot that has been running for weeks must not fill its disk with them.
package incidents

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// SchemaVersion identifies the bundle contract read by the diagnosis tool.
const SchemaVersion = "incident.bundle.v1"

// DefaultDirectory is where bundles land unless a deployment says otherwise.
const DefaultDirectory = "artifacts/incidents"

// DefaultKeep bounds how many bundles one robot keeps. Old failures are still
// useful, but not more useful than a full disk: the newest are kept.
const DefaultKeep = 200

// Environment fingerprints the running system, so an error code can be traced to
// the build, catalogue and calibration that produced it.
type Environment struct {
	RobotID           string `json:"robotId,omitempty"`
	Adapter           string `json:"adapter,omitempty"`
	SoftwareVersion   string `json:"softwareVersion,omitempty"`
	ProtocolVersion   string `json:"protocolVersion,omitempty"`
	CatalogRevision   string `json:"catalogRevision,omitempty"`
	MapID             string `json:"mapId,omitempty"`
	MapRevision       string `json:"mapRevision,omitempty"`
	CalibrationID     string `json:"calibrationRevision,omitempty"`
	WorldRevision     uint64 `json:"worldRevision,omitempty"`
	CollectedAtUnixMS int64  `json:"collectedAtUnixMs"`
}

// StepRun is one durable step record as the execution store holds it.
type StepRun struct {
	StepID         string `json:"stepId"`
	Capability     string `json:"capability,omitempty"`
	SafetyLevel    string `json:"safetyLevel,omitempty"`
	Status         string `json:"status"`
	IdempotencyKey string `json:"idempotencyKey,omitempty"`
}

// Evidence is one archived capture, referenced by identity and hash rather than
// copied: the bundle stays small and the evidence stays the single copy.
type Evidence struct {
	StepID       string `json:"stepId,omitempty"`
	ID           string `json:"id"`
	CaptureID    string `json:"captureId,omitempty"`
	ObservedAtMS int64  `json:"observedAtUnixMs,omitempty"`
	SourceID     string `json:"sourceId,omitempty"`
	RGBSHA256    string `json:"rgbSha256,omitempty"`
	DepthSHA256  string `json:"depthSha256,omitempty"`
}

// TimelineEvent is one task event, flattened to the fields a diagnostician reads.
type TimelineEvent struct {
	Sequence   uint64         `json:"sequence"`
	Type       string         `json:"type"`
	OccurredAt time.Time      `json:"occurredAt"`
	StepID     string         `json:"stepId,omitempty"`
	ToolName   string         `json:"toolName,omitempty"`
	Status     string         `json:"status,omitempty"`
	Error      string         `json:"error,omitempty"`
	Message    string         `json:"message,omitempty"`
	Payload    map[string]any `json:"payload,omitempty"`
}

// Recovery is the verdict the operator sees, recorded because it is the first
// thing anyone asks: can this be resumed, and does it need reconciliation.
type Recovery struct {
	CanResume              bool     `json:"canResume"`
	RequiresReconciliation bool     `json:"requiresReconciliation"`
	ReasonCode             string   `json:"reasonCode,omitempty"`
	Reason                 string   `json:"reason,omitempty"`
	CompletedStepIDs       []string `json:"completedStepIds"`
	UncertainStepIDs       []string `json:"uncertainStepIds"`
}

// Task is the failure's identity and ending.
type Task struct {
	ID           string    `json:"id"`
	Request      string    `json:"request,omitempty"`
	Adapter      string    `json:"adapter,omitempty"`
	Revision     uint64    `json:"revision"`
	State        string    `json:"state"`
	TerminalCode string    `json:"terminalCode,omitempty"`
	EndedAt      time.Time `json:"endedAt"`
}

// Bundle is the whole record.
type Bundle struct {
	SchemaVersion string          `json:"schemaVersion"`
	Task          Task            `json:"task"`
	Recovery      Recovery        `json:"recovery"`
	Environment   Environment     `json:"environment"`
	Timeline      []TimelineEvent `json:"timeline"`
	StepRuns      []StepRun       `json:"stepRuns"`
	Evidence      []Evidence      `json:"evidence"`
	// Timing is the console's latency report verbatim, or null when the
	// deployment does not record step timing. Never invented here.
	Timing      json.RawMessage `json:"timing"`
	CollectedAt time.Time       `json:"collectedAt"`
	// Note explains, in the record itself, why nothing was fixed automatically.
	Note string `json:"note"`
}

// Writer persists bundles for one robot.
type Writer struct {
	Directory string
	Keep      int
	Now       func() time.Time
}

// New returns a writer rooted at directory (defaults to DefaultDirectory).
func New(directory string) *Writer {
	if strings.TrimSpace(directory) == "" {
		directory = DefaultDirectory
	}
	return &Writer{Directory: directory, Keep: DefaultKeep}
}

func (w *Writer) now() time.Time {
	if w.Now != nil {
		return w.Now()
	}
	return time.Now()
}

// Write persists one bundle and prunes older ones. It returns the path written.
//
// Every failure path is silent by contract: a task that ended badly must not be
// made worse by an observability write. Callers get the error so they can log it.
func (w *Writer) Write(bundle Bundle) (string, error) {
	if strings.TrimSpace(bundle.Task.ID) == "" {
		return "", fmt.Errorf("an incident bundle needs a task id")
	}
	if bundle.SchemaVersion == "" {
		bundle.SchemaVersion = SchemaVersion
	}
	if bundle.CollectedAt.IsZero() {
		bundle.CollectedAt = w.now()
	}
	if bundle.Note == "" {
		bundle.Note = "本记录只包含事实：状态、事件、步骤、证据引用、耗时与修订。诊断与建议由 scripts/diagnose_task.py 依据故障族表产出；本记录不修改任何东西。"
	}
	if bundle.Timeline == nil {
		bundle.Timeline = []TimelineEvent{}
	}
	if bundle.StepRuns == nil {
		bundle.StepRuns = []StepRun{}
	}
	if bundle.Evidence == nil {
		bundle.Evidence = []Evidence{}
	}
	payload, err := json.MarshalIndent(bundle, "", "  ")
	if err != nil {
		return "", err
	}
	payload = append(payload, '\n')
	if err := os.MkdirAll(w.Directory, 0o755); err != nil {
		return "", err
	}
	target := filepath.Join(w.Directory, bundle.Task.ID+".bundle.json")
	// Write-then-rename: a reader must never see half an incident.
	temporary := target + ".tmp"
	if err := os.WriteFile(temporary, payload, 0o644); err != nil {
		return "", err
	}
	if err := os.Rename(temporary, target); err != nil {
		_ = os.Remove(temporary)
		return "", err
	}
	w.prune()
	return target, nil
}

// Digest is a stable hash of the facts, so two bundles can be compared for "same
// failure, different run" without diffing large documents.
func Digest(bundle Bundle) string {
	sum := sha256.New()
	for _, event := range bundle.Timeline {
		fmt.Fprintf(sum, "%d|%s|%s|%s\n", event.Sequence, event.Type, event.StepID, event.Error)
	}
	for _, run := range bundle.StepRuns {
		fmt.Fprintf(sum, "%s|%s|%s\n", run.StepID, run.Capability, run.Status)
	}
	fmt.Fprintf(sum, "%s|%s|%s\n", bundle.Task.ID, bundle.Task.State, bundle.Task.TerminalCode)
	return hex.EncodeToString(sum.Sum(nil))[:16]
}

// prune keeps the newest Keep bundles, oldest first by modification time.
func (w *Writer) prune() {
	keep := w.Keep
	if keep <= 0 {
		keep = DefaultKeep
	}
	entries, err := os.ReadDir(w.Directory)
	if err != nil {
		return
	}
	type candidate struct {
		path    string
		modTime time.Time
	}
	bundles := make([]candidate, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".bundle.json") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		bundles = append(bundles, candidate{filepath.Join(w.Directory, entry.Name()), info.ModTime()})
	}
	if len(bundles) <= keep {
		return
	}
	sort.Slice(bundles, func(i, j int) bool { return bundles[i].modTime.Before(bundles[j].modTime) })
	for _, stale := range bundles[:len(bundles)-keep] {
		_ = os.Remove(stale.path)
	}
}
