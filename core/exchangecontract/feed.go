package exchangecontract

import (
	"context"
	"errors"
)

var ErrResyncRequired = errors.New("exchange feed retention gap requires snapshot resync")

// Feed revisions are local durable receipt ordinals, not sender sequence numbers.
// They never reset or get reused after body retention, including an empty inbox.
type FeedItem struct {
	Revision uint64   `json:"revision,string"`
	Envelope Envelope `json:"envelope"`
}
type Feed struct {
	SchemaVersion string     `json:"schemaVersion"`
	After         uint64     `json:"after,string"`
	Next          uint64     `json:"next,string"`
	Head          uint64     `json:"head,string"`
	PrunedThrough uint64     `json:"prunedThrough,string"`
	More          bool       `json:"more"`
	Items         []FeedItem `json:"items"`
}

// FeedJournal is a read extension. Snapshot is transactionally consistent and
// bounded. More=true forbids treating its contents as a complete peer view.
type FeedJournal interface {
	Feed(context.Context, uint64, int) (Feed, error)
	Snapshot(context.Context, int) (Feed, error)
}

// LatestSnapshotJournal optionally supplies a current-state projection without
// changing the historical Feed/Snapshot contracts. Implementations must filter
// the complete recipient and configured sender identities before applying limit,
// and select only the retained body matching each stream's durable incoming
// sequence cursor. A missing latest body must not revive an older body after GC.
// Freshness, payload, task and spatial validation happen AFTER latest selection;
// invalid or expired latest values still supersede older values. Head and
// PrunedThrough must come from the same read transaction as Items. More=true
// means the current stream set is incomplete, never permission to use a subset.
type LatestSnapshotJournal interface {
	LatestSnapshot(context.Context, Identity, []Identity, int) (Feed, error)
}
