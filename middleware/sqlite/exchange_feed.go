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
func (s *ExchangeStore) readFeed(ctx context.Context, after uint64, limit int, snapshot bool) (x.Feed, error) {
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
	rows, err := tx.QueryContext(ctx, `SELECT f.revision,i.envelope FROM exchange_feed f JOIN exchange_inbox i ON i.id=f.message_id WHERE f.revision>? ORDER BY f.revision LIMIT ?`, after, limit+1)
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
