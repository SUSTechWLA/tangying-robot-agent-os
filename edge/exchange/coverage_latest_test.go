package exchange

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

func receiveCoverage(t *testing.T, sender, receiver *Service, request x.Request) x.Envelope {
	t.Helper()
	e, err := sender.Publish(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := receiver.Receive(context.Background(), e.Sender.AgentID, e); err != nil {
		t.Fatal(err)
	}
	return e
}

func TestCoverageLatestSnapshotOutlivesHistoricalLimitAndRestart(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a.db"), identityA, identityB, 1100)
	defer as.Close()
	path := filepath.Join(dir, "b.db")
	b, bs := openService(t, path, identityB, identityA, 1100)
	var latest x.Envelope
	for i := 1; i <= 1005; i++ {
		target := "cell1"
		if i == 1005 {
			target = "cell2"
		}
		latest = receiveCoverage(t, a, b, coverageRequest(fmt.Sprintf("sample-%d", i), target))
	}
	if receipt, err := b.Receive(ctx, identityA.AgentID, latest); err != nil || receipt.Disposition != "duplicate" {
		t.Fatalf("latest retransmission=%+v %v", receipt, err)
	}
	if err := bs.Close(); err != nil {
		t.Fatal(err)
	}
	b, bs = openService(t, path, identityB, identityA, 1100)
	defer bs.Close()
	q := coverageQuery()
	q.Limit = 1
	view, err := b.CoverageView(ctx, q)
	if err != nil || !view.CompleteRead || view.Cursor != 1005 || len(view.Contributions) != 1 || view.Contributions[0].MessageID != latest.MessageID || len(view.ProposedCoveredTargetIDs) != 1 || view.ProposedCoveredTargetIDs[0] != "cell2" {
		t.Fatalf("history disabled current coverage: %+v %v", view, err)
	}
	// Historical readers still see every accepted receipt and their original
	// pagination; the current-state optimization does not truncate the journal.
	history, err := bs.Snapshot(ctx, 1000)
	if err != nil || !history.More || len(history.Items) != 1000 || history.Head != 1005 {
		t.Fatalf("historical snapshot changed: len=%d more=%v head=%d err=%v", len(history.Items), history.More, history.Head, err)
	}
	count, cursor := 0, uint64(0)
	for {
		feed, err := b.Feed(ctx, cursor, 400)
		if err != nil {
			t.Fatal(err)
		}
		for _, item := range feed.Items {
			count++
			if item.Revision != uint64(count) || item.Envelope.Sequence != uint64(count) {
				t.Fatal("history receipt/sequence order changed")
			}
		}
		cursor = feed.Next
		if !feed.More {
			break
		}
	}
	if count != 1005 || cursor != 1005 {
		t.Fatalf("lost historical evidence: count=%d cursor=%d", count, cursor)
	}
}

func TestCoverageLatestSnapshotRejectsCurrentValueWithoutFallback(t *testing.T) {
	for _, tc := range []struct {
		name, reason string
		change       func(*x.Request)
	}{
		{"target", "target_outside_scope", func(r *x.Request) { *r = coverageRequest("new", "outside-scope") }},
		{"basis", "spatial_basis_mismatch", func(r *x.Request) { r.Basis.MapRevision = "v2" }},
		{"kind", "spatial_basis_mismatch", func(r *x.Request) { r.Kind = "mission_status" }},
		{"payload", "unsupported_payload", func(r *x.Request) { r.Payload = json.RawMessage(`{"schemaVersion":"other-report"}`) }},
		{"expired", "envelope_not_fresh", func(r *x.Request) { r.TTLMS = 1 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 10)
			defer as.Close()
			b, bs := openService(t, filepath.Join(dir, "b"), identityB, identityA, 10)
			defer bs.Close()
			old := receiveCoverage(t, a, b, coverageRequest("old", "cell1"))
			r := coverageRequest("new", "cell2")
			tc.change(&r)
			latest := receiveCoverage(t, a, b, r)
			b.now = func() time.Time { return testNow.Add(2 * time.Second) }
			if err := old.Fresh(b.now().UnixMilli()); err != nil {
				t.Fatal("test requires older evidence to remain fresh")
			}
			q := coverageQuery()
			q.Limit = 1
			v, err := b.CoverageView(context.Background(), q)
			if err != nil || !v.CompleteRead || len(v.ProposedCoveredTargetIDs) != 0 || len(v.Rejected) != 1 || v.Rejected[0].MessageID != latest.MessageID || v.Rejected[0].Reason != tc.reason {
				t.Fatalf("older value revived: %+v %v", v, err)
			}
		})
	}
}

// Embedding only the older ports deliberately hides SQLite's optional latest
// snapshot, emulating an unchanged third-party Journal implementation.
type legacyCoverageJournal struct {
	x.Journal
	x.FeedJournal
}

func TestCoverageLegacySnapshotRemainsConservative(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 10)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b"), identityB, identityA, 10)
	defer bs.Close()
	receiveCoverage(t, a, b, coverageRequest("old", "cell1"))
	receiveCoverage(t, a, b, coverageRequest("new", "outside-scope"))
	legacy, err := New(ctx, identityB, []x.Identity{identityA}, legacyCoverageJournal{bs, bs})
	if err != nil {
		t.Fatal(err)
	}
	legacy.now = b.now
	q := coverageQuery()
	q.Limit = 1
	v, err := legacy.CoverageView(ctx, q)
	if err != nil || v.CompleteRead || len(v.Contributions) != 0 || len(v.ProposedCoveredTargetIDs) != 0 {
		t.Fatalf("legacy truncated history became complete: %+v %v", v, err)
	}
	q.Limit = 2
	v, err = legacy.CoverageView(ctx, q)
	if err != nil || !v.CompleteRead || len(v.ProposedCoveredTargetIDs) != 0 || len(v.Rejected) != 2 || v.Rejected[0].Reason != "superseded" {
		t.Fatalf("legacy latest rejection changed: %+v %v", v, err)
	}
}

func TestCoverageLatestSnapshotLimitCountsCurrentStreams(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 10)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b"), identityB, identityA, 10)
	defer bs.Close()
	for _, id := range []string{"one", "two", "three"} {
		receiveCoverage(t, a, b, coverageRequest(id, "cell1"))
	}
	other := coverageRequest("other", "cell2")
	other.Stream = "other-stream"
	receiveCoverage(t, a, b, other)
	q := coverageQuery()
	q.Limit = 1
	v, err := b.CoverageView(ctx, q)
	if err != nil || v.CompleteRead || len(v.ProposedCoveredTargetIDs) != 0 || len(v.Contributions) != 0 || len(v.RemainingTargetIDs) != 3 {
		t.Fatalf("partial current streams used: %+v %v", v, err)
	}
	q.Limit = 2
	v, err = b.CoverageView(ctx, q)
	if err != nil || !v.CompleteRead || len(v.ProposedCoveredTargetIDs) != 2 || len(v.Contributions) != 2 || v.Cursor != 4 {
		t.Fatalf("history counted against current limit: %+v %v", v, err)
	}
}

func TestCoverageLatestSnapshotFiltersFullIdentityBeforeLimit(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(*x.Identity)
	}{
		{"domain", func(i *x.Identity) { i.Domain = "new-domain" }},
		{"robot", func(i *x.Identity) { i.RobotID = "new-robot" }},
		{"agent", func(i *x.Identity) { i.AgentID = "new-agent" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			dir := t.TempDir()
			a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 10)
			defer as.Close()
			b, bs := openService(t, filepath.Join(dir, "b"), identityB, identityA, 10)
			defer bs.Close()
			for _, stream := range []string{"old-one", "old-two"} {
				r := coverageRequest(stream, "cell1")
				r.Stream = stream
				receiveCoverage(t, a, b, r)
			}
			current := identityA
			tc.change(&current)
			changed, err := New(ctx, identityB, []x.Identity{current}, bs)
			if err != nil {
				t.Fatal(err)
			}
			changed.now = b.now
			newSender, newStore := openService(t, filepath.Join(dir, "current"), current, identityB, 10)
			defer newStore.Close()
			r := coverageRequest("current", "cell2")
			r.Stream = "current-stream"
			currentEnvelope := receiveCoverage(t, newSender, changed, r)
			q := coverageQuery()
			q.Limit = 1
			v, err := changed.CoverageView(ctx, q)
			if err != nil || !v.CompleteRead || len(v.Contributions) != 1 || v.Contributions[0].MessageID != currentEnvelope.MessageID || len(v.ProposedCoveredTargetIDs) != 1 || v.ProposedCoveredTargetIDs[0] != "cell2" {
				t.Fatalf("stale identity counted before limit: %+v %v", v, err)
			}
			for _, mutate := range []func(*x.Identity){
				func(i *x.Identity) { i.AgentID = "other-recipient" },
				func(i *x.Identity) { i.RobotID = "other-robot" },
				func(i *x.Identity) { i.Domain = "other-domain" },
			} {
				recipient := identityB
				mutate(&recipient)
				feed, err := bs.LatestSnapshot(ctx, recipient, []x.Identity{current}, 1)
				if err != nil || feed.More || len(feed.Items) != 0 || feed.Head != 3 {
					t.Fatalf("recipient scope leaked: %+v %v", feed, err)
				}
			}
		})
	}
}

func TestCoverageLatestSnapshotPrunedHeadNeverRevivesOlderBody(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 10)
	defer as.Close()
	path := filepath.Join(dir, "b")
	b, bs := openService(t, path, identityB, identityA, 10)
	old := coverageRequest("old", "cell1")
	old.TTLMS = x.MaxTTLMS
	oldEnvelope := receiveCoverage(t, a, b, old)
	latest := coverageRequest("latest", "cell2")
	latest.TTLMS = 1
	receiveCoverage(t, a, b, latest)
	b.now = func() time.Time { return testNow.Add(time.Duration(x.RetentionMS+2) * time.Millisecond) }
	if err := b.Prune(ctx); err != nil {
		t.Fatal(err)
	}
	if err := bs.Close(); err != nil {
		t.Fatal(err)
	}
	b, bs = openService(t, path, identityB, identityA, 10)
	defer bs.Close()
	history, err := bs.Snapshot(ctx, 10)
	if err != nil || len(history.Items) != 1 || history.Items[0].Envelope.MessageID != oldEnvelope.MessageID || history.Head != 2 || history.PrunedThrough != 2 {
		t.Fatalf("mixed TTL retention setup: %+v %v", history, err)
	}
	current, err := bs.LatestSnapshot(ctx, identityB, []x.Identity{identityA}, 1)
	if err != nil || current.More || len(current.Items) != 0 || current.Head != 2 || current.Next != 2 || current.PrunedThrough != 2 {
		t.Fatalf("GC revived older body: %+v %v", current, err)
	}
	if _, err := b.Feed(ctx, 0, 10); !errors.Is(err, x.ErrResyncRequired) {
		t.Fatalf("history GC gap disappeared: %v", err)
	}
	// Even if the wall clock is corrected backwards after restart, the durable
	// sequence tombstone must suppress the older, now apparently fresh body.
	b.now = func() time.Time { return testNow.Add(2 * time.Second) }
	if err := oldEnvelope.Fresh(b.now().UnixMilli()); err != nil {
		t.Fatal(err)
	}
	v, err := b.CoverageView(ctx, coverageQuery())
	if err != nil || !v.CompleteRead || len(v.ProposedCoveredTargetIDs) != 0 {
		t.Fatalf("clock correction resurrected coverage: %+v %v", v, err)
	}
	legacy, err := New(ctx, identityB, []x.Identity{identityA}, legacyCoverageJournal{bs, bs})
	if err != nil {
		t.Fatal(err)
	}
	legacy.now = b.now
	legacyView, err := legacy.CoverageView(ctx, coverageQuery())
	if err != nil || legacyView.CompleteRead || len(legacyView.ProposedCoveredTargetIDs) != 0 || legacyView.PrunedThrough != 2 {
		t.Fatalf("legacy reader revived a body without its durable cursor: %+v %v", legacyView, err)
	}
	a.now = b.now
	receiveCoverage(t, a, b, coverageRequest("after-gc", "cell3"))
	v, err = b.CoverageView(ctx, coverageQuery())
	if err != nil || !v.CompleteRead || v.Cursor != 3 || v.PrunedThrough != 2 || len(v.ProposedCoveredTargetIDs) != 1 || v.ProposedCoveredTargetIDs[0] != "cell3" {
		t.Fatalf("new post-GC current report missing: %+v %v", v, err)
	}
}
