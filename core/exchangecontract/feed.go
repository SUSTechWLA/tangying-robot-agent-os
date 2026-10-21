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
