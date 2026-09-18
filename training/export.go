package training

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"

	_ "modernc.org/sqlite"
)

// Ledger reads the task archive.
//
// It opens the database read-only. An export that could write would eventually be
// run against production, and a training pipeline must never be able to change the
// record it is learning from — that is the same separation this package's doc
// describes, applied to the file handle.
type Ledger struct {
	db *sql.DB
}

// OpenLedger opens a Local Agent database read-only.
func OpenLedger(ctx context.Context, path string) (*Ledger, error) {
	// mode=ro is enforced by the driver, so a bug here fails loudly rather than
	// quietly editing somebody's task history.
	dsn := "file:" + path + "?mode=ro"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open ledger: %w", err)
	}
	db.SetMaxOpenConns(1)
	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("open ledger: %w", err)
	}
	return &Ledger{db: db}, nil
}

// Close releases the database.
func (l *Ledger) Close() error { return l.db.Close() }

// Records reads every task and folds in its steps and evidence.
func (l *Ledger) Records(ctx context.Context) ([]Record, error) {
	records, err := l.readTasks(ctx)
	if err != nil {
		return nil, err
	}
	index := make(map[string]*Record, len(records))
	for position := range records {
		index[records[position].TaskID] = &records[position]
	}
	if err := l.attachSteps(ctx, index); err != nil {
		return nil, err
	}
	if err := l.attachEvidence(ctx, index); err != nil {
		return nil, err
	}
	for position := range records {
		records[position].Classify()
	}
	return records, nil
}

func (l *Ledger) readTasks(ctx context.Context) ([]Record, error) {
	rows, err := l.db.QueryContext(ctx, `
		select id, request, adapter, coalesce(plan_json, ''), state
		from tasks order by created_at`)
	if err != nil {
		return nil, fmt.Errorf("read tasks: %w", err)
	}
	defer rows.Close()
	records := make([]Record, 0)
	for rows.Next() {
		var record Record
		var planJSON string
		if err := rows.Scan(&record.TaskID, &record.Request, &record.Adapter,
			&planJSON, &record.State); err != nil {
			return nil, fmt.Errorf("read tasks: %w", err)
		}
		record.PlanSource, record.Skills = planOf(planJSON)
		records = append(records, record)
	}
	return records, rows.Err()
}

// planOf pulls the plan source and the skill sequence out of the stored bundle.
//
// The shape is read defensively: a plan written by an older build may lack fields
// this one expects, and an export that failed on old rows would quietly export only
// recent history, which is the opposite of what an archive is for.
func planOf(planJSON string) (string, []string) {
	if strings.TrimSpace(planJSON) == "" {
		return "", nil
	}
	var bundle struct {
		Source string `json:"source"`
		Plans  []struct {
			Steps []struct {
				Skill string `json:"skill"`
			} `json:"steps"`
		} `json:"plans"`
	}
	if err := json.Unmarshal([]byte(planJSON), &bundle); err != nil {
		return "", nil
	}
	skills := make([]string, 0)
	for _, plan := range bundle.Plans {
		for _, step := range plan.Steps {
			if step.Skill != "" {
				skills = append(skills, step.Skill)
			}
		}
	}
	return bundle.Source, skills
}

func (l *Ledger) attachSteps(ctx context.Context, index map[string]*Record) error {
	rows, err := l.db.QueryContext(ctx, `
		select task_id, step_id, status, coalesce(capability, ''), coalesce(safety_level, '')
		from step_runs`)
	if err != nil {
		return fmt.Errorf("read step runs: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var taskID string
		var step Step
		if err := rows.Scan(&taskID, &step.StepID, &step.Status,
			&step.Capability, &step.SafetyLevel); err != nil {
			return fmt.Errorf("read step runs: %w", err)
		}
		if record, ok := index[taskID]; ok {
			record.Steps = append(record.Steps, step)
		}
	}
	return rows.Err()
}

func (l *Ledger) attachEvidence(ctx context.Context, index map[string]*Record) error {
	rows, err := l.db.QueryContext(ctx, `
		select task_id, id, coalesce(step_id, ''), coalesce(capture_id, '')
		from observation_evidence where expired = 0`)
	if err != nil {
		// A deployment that never captured evidence has no such rows, and an
		// export must still work: "no evidence" is itself the finding.
		if strings.Contains(strings.ToLower(err.Error()), "no such table") {
			return nil
		}
		return fmt.Errorf("read evidence: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var taskID string
		var evidence Evidence
		if err := rows.Scan(&taskID, &evidence.ID, &evidence.StepID, &evidence.Capture); err != nil {
			return fmt.Errorf("read evidence: %w", err)
		}
		if record, ok := index[taskID]; ok {
			record.Evidence = append(record.Evidence, evidence)
		}
	}
	return rows.Err()
}

// Dataset is the three files a post-training run consumes.
//
// They are separated because they are used differently and must not be mixed: an
// SFT run on the refusal set would teach the model to decline, and a preference
// run needs pairs rather than examples.
type Dataset struct {
	// SFT is (request → plan) pairs the closed loop confirmed.
	SFT []Record `json:"sft"`
	// Refusals are requests the system declined. **They are not optional**, and
	// they are typed differently from the other two sets on purpose.
	//
	// A refusal is not a task. The service rejects the request at creation and
	// stores nothing, so no amount of reading the ledger can produce one — which is
	// why this field is []Refusal and not []Record. The type states the fact that
	// the comment would otherwise have to be trusted about.
	//
	// A fine-tune trained only on successful plans learns to always produce one,
	// and the ability to decline is the safety property most easily lost in
	// distillation. See HarvestRefusals.
	Refusals []Refusal `json:"refusals"`
	// Negative is failed attempts, kept for preference pairs and for evaluation
	// cases mined from real failures.
	Negative []Record `json:"negative"`
	// Excluded counts what was left out and why. It is part of the dataset rather
	// than a log line, because a set that silently dropped a third of its inputs
	// looks bigger than it is.
	Excluded map[string]int `json:"excluded"`
}

// Split sorts records into the three sets, de-duplicating by request fingerprint.
//
// De-duplication is per set and by fingerprint: 64 tasks that are 15 requests must
// not become 64 training rows, and a request that both succeeded and failed must
// not appear as a positive and a negative at once — the model cannot learn both.
// When a request has any verified record, the verified one wins, because success
// is the thing being taught.
func Split(records []Record) Dataset {
	dataset := Dataset{Excluded: map[string]int{}}

	best := map[string]Record{}
	order := make([]string, 0, len(records))
	for _, record := range records {
		key := Fingerprint(record.Request)
		current, seen := best[key]
		if !seen {
			best[key] = record
			order = append(order, key)
			continue
		}
		if rank(record.Verdict) < rank(current.Verdict) {
			best[key] = record
		}
	}

	for _, key := range order {
		record := best[key]
		switch record.Verdict {
		case VerdictVerified:
			if len(record.Skills) == 0 {
				// A verified task whose plan carries no skills teaches the outcome
				// but not the orchestration. It belongs in a future
				// outcome-modelling set, not in an orchestration SFT set, and
				// putting it here would pad the count with rows that contain
				// nothing to learn.
				dataset.Excluded["verified-but-no-plan"]++
				continue
			}
			dataset.SFT = append(dataset.SFT, record)
		case VerdictRefused:
			// Unreachable through the ledger reader: Classify never returns this,
			// because a refused request has no task row to classify. Kept so that a
			// future reader which *can* see rejections — a log, a ledger that starts
			// recording them — lands in the right set instead of silently in the
			// negative one.
			dataset.Excluded["refused-in-ledger-unexpected"]++
		case VerdictFailed:
			dataset.Negative = append(dataset.Negative, record)
		case VerdictUnknownOutcome:
			// Excluded from the negative set as well, which is not the same as
			// being ignored. Labelling an unchecked physical action as a failure
			// teaches the model to avoid the action whose effect nobody confirmed —
			// and the cheapest way to stop having unconfirmed effects is to prefer
			// actions that report success quickly, which is a reward-hacking
			// direction rather than a safety improvement.
			//
			// These records are still valuable, as evaluation cases and as the
			// signal that the evidence gate is firing. They are counted in
			// Quantification.UnknownOutcomes and named here so the exclusion is
			// visible in the manifest.
			dataset.Excluded["unknown-outcome (两半都不计入)"]++
		default:
			dataset.Excluded[string(record.Verdict)]++
		}
	}
	// The zero-count line above is noise; drop any empty bucket.
	for key, count := range dataset.Excluded {
		if count == 0 {
			delete(dataset.Excluded, key)
		}
	}
	return dataset
}

// rank orders verdicts by which record should represent a request. Lower wins.
func rank(verdict Verdict) int {
	switch verdict {
	case VerdictVerified:
		return 0
	case VerdictRefused:
		return 1
	case VerdictFailed:
		return 2
	case VerdictUnknownOutcome:
		return 3
	default:
		return 4
	}
}
