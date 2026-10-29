package sqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

func (s *ExchangeStore) Feed(ctx context.Context, after uint64, limit int) (x.Feed, error) {
	return s.readFeed(ctx, after, limit, false)
}
func (s *ExchangeStore) Snapshot(ctx context.Context, limit int) (x.Feed, error) {
	return s.readFeed(ctx, 0, limit, true)
}

// LatestSnapshot reads a bounded projection of current configured streams. The
// permanent cursor, rather than MAX(sequence) over retained bodies, prevents GC
// from resurrecting an older report. No TTL or business-validity filtering may
// happen here: an expired or invalid newest report must still suppress its past.
func (s *ExchangeStore) LatestSnapshot(ctx context.Context, recipient x.Identity, senders []x.Identity, limit int) (x.Feed, error) {
	if err := recipient.Validate(); err != nil {
		return x.Feed{}, err
	}
	if len(senders) < 1 || len(senders) > 256 {
		return x.Feed{}, errors.New("latest snapshot requires 1..256 senders")
	}
	seen := make(map[string]bool, len(senders))
	for _, sender := range senders {
		if err := sender.Validate(); err != nil {
			return x.Feed{}, err
		}
		if seen[sender.AgentID] || sender.AgentID == recipient.AgentID {
			return x.Feed{}, errors.New("latest snapshot senders must be unique and different from recipient")
		}
		seen[sender.AgentID] = true
	}
	raw, err := json.Marshal(senders)
	if err != nil {
		return x.Feed{}, err
	}
	query := `SELECT f.revision,i.envelope FROM exchange_cursor c
	JOIN exchange_inbox i ON i.stream=c.stream AND i.sequence=c.sequence
	JOIN exchange_feed f ON f.message_id=i.id
	WHERE c.direction='in'
	AND json_extract(i.envelope,'$.recipient.agentId')=?
	AND json_extract(i.envelope,'$.recipient.robotId')=?
	AND json_extract(i.envelope,'$.recipient.domain')=?
	AND EXISTS(SELECT 1 FROM json_each(?) p
		WHERE json_extract(p.value,'$.agentId')=json_extract(i.envelope,'$.sender.agentId')
		AND json_extract(p.value,'$.robotId')=json_extract(i.envelope,'$.sender.robotId')
		AND json_extract(p.value,'$.domain')=json_extract(i.envelope,'$.sender.domain'))
	ORDER BY f.revision LIMIT ?`
	return s.readFeedRows(ctx, 0, limit, true, query, []any{recipient.AgentID, recipient.RobotID, recipient.Domain, string(raw), limit + 1})
}

func (s *ExchangeStore) readFeed(ctx context.Context, after uint64, limit int, snapshot bool) (x.Feed, error) {
	return s.readFeedRows(ctx, after, limit, snapshot,
		`SELECT f.revision,i.envelope FROM exchange_feed f JOIN exchange_inbox i ON i.id=f.message_id WHERE f.revision>? ORDER BY f.revision LIMIT ?`,
		[]any{after, limit + 1})
}

func (s *ExchangeStore) readFeedRows(ctx context.Context, after uint64, limit int, snapshot bool, query string, args []any) (x.Feed, error) {
	if limit < 1 || limit > 1000 || after > 1<<63-1 {
		return x.Feed{}, errors.New("invalid feed cursor or limit")
	}
	tx, err := s.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return x.Feed{}, err
	}
	defer tx.Rollback()
	f := x.Feed{SchemaVersion: "agent.data.feed.v1", After: after, Next: after, Items: []x.FeedItem{}}
	err = tx.QueryRowContext(ctx, `SELECT COALESCE((SELECT seq FROM sqlite_sequence WHERE name='exchange_feed'),0),COALESCE((SELECT value FROM exchange_meta WHERE key='feed_pruned_through'),0)`).Scan(&f.Head, &f.PrunedThrough)
	if err != nil {
		return f, err
	}
	if !snapshot && after < f.PrunedThrough {
		return f, x.ErrResyncRequired
	}
	if after > f.Head {
		return f, errors.New("feed cursor exceeds journal head")
	}
	rows, err := tx.QueryContext(ctx, query, args...)
	if err != nil {
		return f, err
	}
	defer rows.Close()
	for rows.Next() {
		var item x.FeedItem
		var raw []byte
		if err := rows.Scan(&item.Revision, &raw); err != nil {
			return f, err
		}
		if len(f.Items) == limit {
			f.More = true
			break
		}
		if err := json.Unmarshal(raw, &item.Envelope); err != nil {
			return f, err
		}
		f.Items = append(f.Items, item)
		f.Next = item.Revision
	}
	if err := rows.Err(); err != nil {
		return f, err
	}
	if !f.More {
		f.Next = f.Head
	}
	if err := rows.Close(); err != nil {
		return f, err
	}
	return f, tx.Commit()
}

var _ x.FeedJournal = (*ExchangeStore)(nil)
var _ x.LatestSnapshotJournal = (*ExchangeStore)(nil)
