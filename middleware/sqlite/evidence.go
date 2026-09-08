package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var _ tasks.EvidenceStore = (*Store)(nil)

func ensureEvidenceSchema(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS observation_evidence (
		record_index INTEGER PRIMARY KEY AUTOINCREMENT,
		id TEXT NOT NULL UNIQUE,
		task_id TEXT NOT NULL,
		step_id TEXT NOT NULL,
		capture_id TEXT NOT NULL,
		metadata_json BLOB NOT NULL,
		snapshot_json BLOB,
		rgb BLOB,
		depth BLOB,
		content_bytes INTEGER NOT NULL,
		expired INTEGER NOT NULL DEFAULT 0,
		expired_at TEXT,
		UNIQUE (task_id, step_id, capture_id),
		FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
	);
	CREATE INDEX IF NOT EXISTS observation_evidence_task_idx ON observation_evidence(task_id, record_index DESC);`)
	return err
}

func (s *Store) RecordEvidence(ctx context.Context, snapshot telemetry.Snapshot) (tasks.EvidenceRecord, error) {
	record, err := tasks.NewEvidence(snapshot, time.Now())
	if err != nil {
		return tasks.EvidenceRecord{}, err
	}
	s.evidenceMu.Lock()
	defer s.evidenceMu.Unlock()
	for attempt := 0; ; attempt++ {
		stored, err := s.recordEvidence(ctx, record)
		var coded interface{ Code() int }
		if err == nil || attempt >= 7 || !errors.As(err, &coded) || (coded.Code()&255 != 5 && coded.Code()&255 != 6) {
			return stored, err
		}
		// Another local task/revision transaction can briefly own the writer.
		// Retry only SQLite BUSY/LOCKED, using a new transaction and unchanged
		// capture bytes; disk errors/conflicts are never hidden or replayed.
		delay := time.Duration(1<<attempt) * 10 * time.Millisecond
		if delay > 100*time.Millisecond {
			delay = 100 * time.Millisecond
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return tasks.EvidenceRecord{}, ctx.Err()
		case <-timer.C:
		}
	}
}

func (s *Store) recordEvidence(ctx context.Context, record tasks.EvidenceRecord) (tasks.EvidenceRecord, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return tasks.EvidenceRecord{}, err
	}
	defer tx.Rollback()
	var taskExists int
	if err := tx.QueryRowContext(ctx, `SELECT 1 FROM tasks WHERE id = ?`, record.TaskID).Scan(&taskExists); errors.Is(err, sql.ErrNoRows) {
		return tasks.EvidenceRecord{}, tasks.ErrTaskNotFound
	} else if err != nil {
		return tasks.EvidenceRecord{}, err
	}
	previous, err := scanEvidence(tx.QueryRowContext(ctx, evidenceSelect+` WHERE task_id = ? AND id = ?`, record.TaskID, record.ID))
	if err == nil {
		if previous.SnapshotSHA256 != record.SnapshotSHA256 || previous.RGBSHA256 != record.RGBSHA256 || previous.DepthSHA256 != record.DepthSHA256 {
			return tasks.EvidenceRecord{}, tasks.ErrEvidenceConflict
		}
		return previous, nil // immutable replay never rehydrates expired content
	}
	if !errors.Is(err, tasks.ErrEvidenceNotFound) {
		return tasks.EvidenceRecord{}, err
	}
	metadata := record
	metadata.Snapshot, metadata.RGB, metadata.Depth = nil, nil, nil
	metadataJSON, err := json.Marshal(metadata)
	if err != nil {
		return tasks.EvidenceRecord{}, err
	}
	result, err := tx.ExecContext(ctx, `INSERT INTO observation_evidence
		(id, task_id, step_id, capture_id, metadata_json, snapshot_json, rgb, depth, content_bytes)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`, record.ID, record.TaskID, record.StepID, record.CaptureID, metadataJSON, record.Snapshot, record.RGB, record.Depth, record.SnapshotBytes+record.RGBBytes+record.DepthBytes)
	if err != nil {
		return tasks.EvidenceRecord{}, err
	}
	record.RecordIndex, err = result.LastInsertId()
	if err != nil {
		return tasks.EvidenceRecord{}, err
	}
	// Retain both a capture-count and a logical-byte budget. Metadata and hashes
	// remain; expiration never substitutes a different or newly stamped frame.
	if err := expireEvidence(ctx, tx, time.Now(), tasks.MaxEvidenceCaptures, tasks.MaxEvidenceRetainedBytes); err != nil {
		return tasks.EvidenceRecord{}, err
	}
	if err := tx.Commit(); err != nil {
		return tasks.EvidenceRecord{}, err
	}
	return record, nil
}

func expireEvidence(ctx context.Context, tx *sql.Tx, now time.Time, maxCaptures, maxBytes int) error {
	_, err := tx.ExecContext(ctx, `WITH retained AS (
		SELECT record_index, ROW_NUMBER() OVER (ORDER BY record_index DESC) AS position,
		SUM(content_bytes) OVER (ORDER BY record_index DESC) AS retained_bytes
		FROM observation_evidence WHERE expired = 0
	) UPDATE observation_evidence SET snapshot_json = NULL, rgb = NULL, depth = NULL,
		content_bytes = 0, expired = 1, expired_at = ?
	WHERE record_index IN (SELECT record_index FROM retained WHERE position > ? OR retained_bytes > ?)`,
		now.UTC().Format(time.RFC3339Nano), maxCaptures, maxBytes)
	return err
}

const evidenceSelect = `SELECT record_index, metadata_json, snapshot_json, rgb, depth, expired, expired_at FROM observation_evidence`

type evidenceScanner interface{ Scan(...any) error }

func scanEvidence(row evidenceScanner) (tasks.EvidenceRecord, error) {
	var record tasks.EvidenceRecord
	var metadata, snapshot, rgb, depth []byte
	var index int64
	var expired bool
	var expiredAt sql.NullString
	err := row.Scan(&index, &metadata, &snapshot, &rgb, &depth, &expired, &expiredAt)
	if errors.Is(err, sql.ErrNoRows) {
		return record, tasks.ErrEvidenceNotFound
	}
	if err != nil {
		return record, err
	}
	if err := json.Unmarshal(metadata, &record); err != nil {
		return record, fmt.Errorf("%w: metadata", tasks.ErrEvidenceCorrupt)
	}
	record.RecordIndex, record.Expired = index, expired
	if expiredAt.Valid {
		stamp, err := time.Parse(time.RFC3339Nano, expiredAt.String)
		if err != nil {
			return record, fmt.Errorf("%w: expiration metadata", tasks.ErrEvidenceCorrupt)
		}
		record.ExpiredAt = &stamp
	}
	if expired {
		return record, nil
	}
	if tasks.EvidenceHash(snapshot) != record.SnapshotSHA256 || tasks.EvidenceHash(rgb) != record.RGBSHA256 || tasks.EvidenceHash(depth) != record.DepthSHA256 {
		return record, tasks.ErrEvidenceCorrupt
	}
	record.Snapshot, record.RGB, record.Depth = snapshot, rgb, depth
	return record, nil
}

func (s *Store) Evidence(ctx context.Context, taskID, evidenceID string) (tasks.EvidenceRecord, error) {
	if !tasks.ValidEvidenceKey(taskID) || !tasks.ValidEvidenceKey(evidenceID) {
		return tasks.EvidenceRecord{}, tasks.ErrEvidenceNotFound
	}
	return scanEvidence(s.db.QueryRowContext(ctx, evidenceSelect+` WHERE task_id = ? AND id = ?`, taskID, evidenceID))
}

func (s *Store) ListEvidence(ctx context.Context, taskID string, limit int, beforeIndex int64) ([]tasks.EvidenceRecord, error) {
	if !tasks.ValidEvidenceKey(taskID) || limit < 1 || limit > 200 || beforeIndex < 0 {
		return nil, tasks.ErrEvidenceInvalid
	}
	rows, err := s.db.QueryContext(ctx, `SELECT record_index, metadata_json, expired, expired_at
		FROM observation_evidence WHERE task_id = ? AND (? = 0 OR record_index < ?)
		ORDER BY record_index DESC LIMIT ?`, taskID, beforeIndex, beforeIndex, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	records := []tasks.EvidenceRecord{}
	for rows.Next() {
		var record tasks.EvidenceRecord
		var metadata []byte
		var index int64
		var expired bool
		var expiredAt sql.NullString
		if err := rows.Scan(&index, &metadata, &expired, &expiredAt); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(metadata, &record); err != nil {
			return nil, fmt.Errorf("%w: metadata", tasks.ErrEvidenceCorrupt)
		}
		record.RecordIndex, record.Expired = index, expired
		if expiredAt.Valid {
			stamp, err := time.Parse(time.RFC3339Nano, expiredAt.String)
			if err != nil {
				return nil, tasks.ErrEvidenceCorrupt
			}
			record.ExpiredAt = &stamp
		}
		records = append(records, record)
	}
	return records, rows.Err()
}
