package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"path/filepath"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

// ExchangeStore uses its own bounded journal, independent of task execution.
// Capacity is fail-closed. Body retention never deletes identity or cursors.
type ExchangeStore struct {
	db       *sql.DB
	capacity int
}

func OpenExchange(path string, capacity int) (*ExchangeStore, error) {
	if capacity < 1 || capacity > 100000 {
		return nil, errors.New("exchange capacity must be 1..100000")
	}
	if path == "" || path == ":memory:" {
		return nil, errors.New("exchange requires a durable file path")
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	u := url.URL{Scheme: "file", Path: filepath.ToSlash(absolute)}
	q := u.Query()
	q.Add("_pragma", "busy_timeout(5000)")
	q.Add("_pragma", "journal_mode(WAL)")
	q.Add("_pragma", "synchronous(FULL)")
	u.RawQuery = q.Encode()
	db, err := sql.Open("sqlite", u.String())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`
	CREATE TABLE IF NOT EXISTS exchange_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
	INSERT OR IGNORE INTO exchange_meta(key,value) VALUES ('schema','agent.data.v1');
	CREATE TABLE IF NOT EXISTS exchange_cursor (direction TEXT NOT NULL, stream TEXT NOT NULL, sequence INTEGER NOT NULL, PRIMARY KEY(direction,stream));
	CREATE TABLE IF NOT EXISTS exchange_inbox (id TEXT PRIMARY KEY, stream TEXT NOT NULL, sequence INTEGER NOT NULL, envelope BLOB NOT NULL, received_ms INTEGER NOT NULL, UNIQUE(stream,sequence));
	CREATE TABLE IF NOT EXISTS exchange_outbox (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, stream TEXT NOT NULL, sequence INTEGER NOT NULL, envelope BLOB NOT NULL, expires_ms INTEGER NOT NULL, acked_ms INTEGER, UNIQUE(stream,sequence));
	CREATE TABLE IF NOT EXISTS exchange_feed (revision INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL);
	CREATE INDEX IF NOT EXISTS exchange_pending_idx ON exchange_outbox(acked_ms,expires_ms);
	`)
	if err != nil {
		db.Close()
		return nil, err
	}
	var schema string
	if err := db.QueryRow(`SELECT value FROM exchange_meta WHERE key='schema'`).Scan(&schema); err != nil {
		db.Close()
		return nil, err
	}
	if schema != x.Schema {
		db.Close()
		return nil, errors.New("unsupported exchange journal schema")
	}
	// A one-time migration assigns existing inbox bodies durable receipt ordinals.
	// Taking the same write lock as admission prevents interleaved backfills.
	s := &ExchangeStore{db: db, capacity: capacity}
	tx, err := s.transaction(context.Background())
	if err == nil {
		_, err = tx.Exec(`INSERT INTO exchange_feed(message_id) SELECT id FROM exchange_inbox WHERE NOT EXISTS(SELECT 1 FROM exchange_feed f WHERE f.message_id=exchange_inbox.id) ORDER BY rowid`)
		if err == nil {
			err = tx.Commit()
		} else {
			tx.Rollback()
		}
	}
	if err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *ExchangeStore) Close() error { return s.db.Close() }

// Taking the write lock before reading cursors also serializes independent
// processes sharing this journal. A deferred read-then-write transaction would
// otherwise race or fail to upgrade its SQLite lock.
func (s *ExchangeStore) transaction(ctx context.Context) (*sql.Tx, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	if _, err = tx.ExecContext(ctx, `UPDATE exchange_meta SET value=value WHERE key='schema'`); err != nil {
		tx.Rollback()
		return nil, err
	}
	return tx, nil
}

func (s *ExchangeStore) Bind(ctx context.Context, identity x.Identity) error {
	if err := identity.Validate(); err != nil {
		return err
	}
	raw, _ := json.Marshal(identity)
	tx, err := s.transaction(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var existing string
	err = tx.QueryRowContext(ctx, `SELECT value FROM exchange_meta WHERE key='identity'`).Scan(&existing)
	if err == nil && existing != string(raw) {
		return fmt.Errorf("%w: journal belongs to a different agent identity", x.ErrConflict)
	}
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if _, err := tx.ExecContext(ctx, `INSERT OR IGNORE INTO exchange_meta(key,value) VALUES ('identity',?)`, string(raw)); err != nil {
		return err
	}
	return tx.Commit()
}

func exchangeStream(e x.Envelope) string {
	raw, _ := json.Marshal([]string{e.Sender.AgentID, e.Sender.Domain, e.Recipient.AgentID, e.Recipient.Domain, e.Stream})
	return string(raw)
}

func exchangeCapacity(ctx context.Context, tx *sql.Tx, table string, capacity int) error {
	var count int
	if err := tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM "+table).Scan(&count); err != nil {
		return err
	}
	if count >= capacity {
		return x.ErrCapacity
	}
	return nil
}

func exchangeCursor(ctx context.Context, tx *sql.Tx, direction, stream string) (uint64, bool, error) {
	var sequence uint64
	err := tx.QueryRowContext(ctx, `SELECT sequence FROM exchange_cursor WHERE direction=? AND stream=?`, direction, stream).Scan(&sequence)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	return sequence, err == nil, err
}

func (s *ExchangeStore) Publish(ctx context.Context, requestID, requestHash string, e x.Envelope, nowMS int64) (x.Envelope, error) {
	tx, err := s.transaction(ctx)
	if err != nil {
		return x.Envelope{}, err
	}
	defer tx.Rollback()
	var hash string
	var raw []byte
	err = tx.QueryRowContext(ctx, `SELECT request_hash,envelope FROM exchange_outbox WHERE request_id=?`, requestID).Scan(&hash, &raw)
	if err == nil {
		if hash != requestHash {
			return x.Envelope{}, x.ErrConflict
		}
		err = json.Unmarshal(raw, &e)
		return e, err
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return x.Envelope{}, err
	}
	if err := exchangeCapacity(ctx, tx, "exchange_outbox", s.capacity); err != nil {
		return x.Envelope{}, err
	}
	stream := exchangeStream(e)
	sequence, exists, err := exchangeCursor(ctx, tx, "out", stream)
	if err != nil {
		return x.Envelope{}, err
	}
	if !exists {
		if err := exchangeCapacity(ctx, tx, "exchange_cursor", s.capacity*2); err != nil {
			return x.Envelope{}, err
		}
	}
	e.Sequence = sequence + 1
	e.MessageID, err = e.Digest()
	if err != nil {
		return x.Envelope{}, err
	}
	if err := e.Validate(); err != nil {
		return x.Envelope{}, err
	}
	if err := e.Fresh(nowMS); err != nil {
		return x.Envelope{}, err
	}
	raw, err = json.Marshal(e)
	if err != nil {
		return x.Envelope{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO exchange_outbox(id,request_id,request_hash,stream,sequence,envelope,expires_ms) VALUES(?,?,?,?,?,?,?)`, e.MessageID, requestID, requestHash, stream, e.Sequence, raw, e.ExpiresAtMS); err != nil {
		return x.Envelope{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO exchange_cursor(direction,stream,sequence) VALUES('out',?,?) ON CONFLICT(direction,stream) DO UPDATE SET sequence=excluded.sequence`, stream, e.Sequence); err != nil {
		return x.Envelope{}, err
	}
	return e, tx.Commit()
}

func (s *ExchangeStore) Accept(ctx context.Context, e x.Envelope, nowMS int64) (x.Receipt, error) {
	if err := e.Validate(); err != nil {
		return x.Receipt{}, err
	}
	tx, err := s.transaction(ctx)
	if err != nil {
		return x.Receipt{}, err
	}
	defer tx.Rollback()
	receipt := x.Receipt{SchemaVersion: x.Schema, MessageID: e.MessageID, Sequence: e.Sequence, Disposition: "duplicate"}
	var existing string
	err = tx.QueryRowContext(ctx, `SELECT id FROM exchange_inbox WHERE id=?`, e.MessageID).Scan(&existing)
	if err == nil {
		return receipt, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return x.Receipt{}, err
	}
	if err := e.Fresh(nowMS); err != nil {
		return x.Receipt{}, err
	}
	stream := exchangeStream(e)
	sequence, exists, err := exchangeCursor(ctx, tx, "in", stream)
	if err != nil {
		return x.Receipt{}, err
	}
	if exists && e.Sequence <= sequence {
		err := tx.QueryRowContext(ctx, `SELECT id FROM exchange_inbox WHERE stream=? AND sequence=?`, stream, e.Sequence).Scan(&existing)
		if err == nil {
			return x.Receipt{}, x.ErrConflict
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return x.Receipt{}, err
		}
		return x.Receipt{}, x.ErrStale
	}
	if err := exchangeCapacity(ctx, tx, "exchange_inbox", s.capacity); err != nil {
		return x.Receipt{}, err
	}
	if !exists {
		if err := exchangeCapacity(ctx, tx, "exchange_cursor", s.capacity*2); err != nil {
			return x.Receipt{}, err
		}
	}
	raw, err := json.Marshal(e)
	if err != nil {
		return x.Receipt{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO exchange_inbox(id,stream,sequence,envelope,received_ms) VALUES(?,?,?,?,?)`, e.MessageID, stream, e.Sequence, raw, nowMS); err != nil {
		return x.Receipt{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO exchange_cursor(direction,stream,sequence) VALUES('in',?,?) ON CONFLICT(direction,stream) DO UPDATE SET sequence=excluded.sequence`, stream, e.Sequence); err != nil {
		return x.Receipt{}, err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO exchange_feed(message_id) VALUES(?)`, e.MessageID); err != nil {
		return x.Receipt{}, err
	}
	receipt.Disposition = "accepted"
	return receipt, tx.Commit()
}

func (s *ExchangeStore) Pending(ctx context.Context, nowMS int64, limit int) ([]x.Envelope, error) {
	return s.pending(ctx, nowMS, limit, "", nil)
}

// PendingFor keeps old envelopes for retention/audit while excluding their
// identities BEFORE FIFO and per-peer head selection. It does not acknowledge
// them or rewrite their identity after a peer configuration change.
func (s *ExchangeStore) PendingFor(ctx context.Context, sender x.Identity, peers []x.Identity, nowMS int64, limit int) ([]x.Envelope, error) {
	if err := sender.Validate(); err != nil {
		return nil, err
	}
	if len(peers) < 1 || len(peers) > 256 {
		return nil, errors.New("pending selection requires 1..256 peers")
	}
	seen := make(map[string]bool, len(peers))
	for _, peer := range peers {
		if err := peer.Validate(); err != nil {
			return nil, err
		}
		if seen[peer.AgentID] || peer.AgentID == sender.AgentID {
			return nil, errors.New("pending peer identities must be unique and different from sender")
		}
		seen[peer.AgentID] = true
	}
	raw, err := json.Marshal(peers)
	if err != nil {
		return nil, err
	}
	filter := ` AND json_extract(a.envelope,'$.sender.agentId')=?
	AND json_extract(a.envelope,'$.sender.robotId')=?
	AND json_extract(a.envelope,'$.sender.domain')=?
	AND EXISTS(SELECT 1 FROM json_each(?) p
		WHERE json_extract(p.value,'$.agentId')=json_extract(a.envelope,'$.recipient.agentId')
		AND json_extract(p.value,'$.robotId')=json_extract(a.envelope,'$.recipient.robotId')
		AND json_extract(p.value,'$.domain')=json_extract(a.envelope,'$.recipient.domain'))`
	return s.pending(ctx, nowMS, limit, filter, []any{sender.AgentID, sender.RobotID, sender.Domain, string(raw)})
}

func (s *ExchangeStore) pending(ctx context.Context, nowMS int64, limit int, identityFilter string, identityArgs []any) ([]x.Envelope, error) {
	if limit < 1 || limit > 1000 {
		return nil, errors.New("invalid pending limit")
	}
	// At most one unacknowledged, unexpired envelope per stream: a failed first
	// delivery must not allow its successor to advance the remote cursor.
	// Select one stream head per recipient. A disconnected recipient cannot
	// occupy every slot and starve the next healthy peer behind its backlog.
	args := append([]any{nowMS}, identityArgs...)
	args = append(args, limit)
	rows, err := s.db.QueryContext(ctx, `WITH eligible AS (
	SELECT a.envelope,a.rowid AS ordinal,a.stream,a.sequence FROM exchange_outbox a
	WHERE a.acked_ms IS NULL AND a.expires_ms>?`+identityFilter+`
	), heads AS (
	SELECT a.envelope,a.ordinal,ROW_NUMBER() OVER(PARTITION BY json_extract(a.envelope,'$.recipient.agentId') ORDER BY a.ordinal) AS peer_rank
	FROM eligible a WHERE NOT EXISTS(SELECT 1 FROM eligible b WHERE b.stream=a.stream AND b.sequence<a.sequence)
	) SELECT envelope FROM heads WHERE peer_rank=1 ORDER BY ordinal LIMIT ?`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanExchange(rows)
}

func (s *ExchangeStore) Acknowledge(ctx context.Context, e x.Envelope, nowMS int64) error {
	result, err := s.db.ExecContext(ctx, `UPDATE exchange_outbox SET acked_ms=COALESCE(acked_ms,?) WHERE id=? AND sequence=?`, nowMS, e.MessageID, e.Sequence)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count != 1 {
		return x.ErrConflict
	}
	return nil
}

func scanExchange(rows *sql.Rows) ([]x.Envelope, error) {
	result := make([]x.Envelope, 0)
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		var e x.Envelope
		if err := json.Unmarshal(raw, &e); err != nil {
			return nil, err
		}
		result = append(result, e)
	}
	return result, rows.Err()
}

func (s *ExchangeStore) Inbox(ctx context.Context, limit int) ([]x.Envelope, error) {
	if limit < 1 || limit > 1000 {
		return nil, errors.New("invalid inbox limit")
	}
	rows, err := s.db.QueryContext(ctx, `SELECT envelope FROM exchange_inbox ORDER BY rowid DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanExchange(rows)
}

func (s *ExchangeStore) Counts(ctx context.Context, nowMS int64) (x.Counts, error) {
	var c x.Counts
	err := s.db.QueryRowContext(ctx, `SELECT (SELECT COUNT(*) FROM exchange_inbox),COUNT(CASE WHEN acked_ms IS NULL AND expires_ms>? THEN 1 END),COUNT(acked_ms),COUNT(CASE WHEN acked_ms IS NULL AND expires_ms<=? THEN 1 END),COALESCE((SELECT value FROM exchange_meta WHERE key='pruned_inbox'),0),COALESCE((SELECT value FROM exchange_meta WHERE key='pruned_outbox'),0) FROM exchange_outbox`, nowMS, nowMS).Scan(&c.Inbox, &c.Pending, &c.Delivered, &c.Expired, &c.PrunedInbox, &c.PrunedOutbox)
	return c, err
}

func (s *ExchangeStore) Prune(ctx context.Context, nowMS int64) error {
	tx, err := s.transaction(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// Conservative gap watermark: any removed unseen receipt requires a resync,
	// even when different TTLs leave earlier receipts retained.
	if _, err := tx.ExecContext(ctx, `INSERT INTO exchange_meta(key,value) VALUES('feed_pruned_through',COALESCE((SELECT MAX(f.revision) FROM exchange_feed f JOIN exchange_inbox i ON i.id=f.message_id WHERE CAST(json_extract(i.envelope,'$.expiresAtMs') AS INTEGER)<=?),0)) ON CONFLICT(key) DO UPDATE SET value=MAX(CAST(value AS INTEGER),CAST(excluded.value AS INTEGER))`, nowMS-x.RetentionMS); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM exchange_feed WHERE message_id IN (SELECT id FROM exchange_inbox WHERE CAST(json_extract(envelope,'$.expiresAtMs') AS INTEGER)<=?)`, nowMS-x.RetentionMS); err != nil {
		return err
	}
	// Incoming expiresAtMs is decoded once at GC time. Schema/field validity was
	// checked before admission. Deleted sequence identities remain protected by
	// their permanent stream cursor even after exact-ID receipt retention ends.
	for _, entry := range []struct{ table, condition, key string }{
		{"exchange_inbox", `CAST(json_extract(envelope,'$.expiresAtMs') AS INTEGER) <= ?`, "pruned_inbox"},
		{"exchange_outbox", `expires_ms <= ?`, "pruned_outbox"},
	} {
		result, err := tx.ExecContext(ctx, "DELETE FROM "+entry.table+" WHERE "+entry.condition, nowMS-x.RetentionMS)
		if err != nil {
			return err
		}
		count, err := result.RowsAffected()
		if err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO exchange_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+CAST(excluded.value AS INTEGER)`, entry.key, count); err != nil {
			return err
		}
	}
	return tx.Commit()
}

var _ x.Journal = (*ExchangeStore)(nil)
