package exchange

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

func coverageQuery() x.CoverageQuery {
	return x.CoverageQuery{SchemaVersion: x.CoverageQuerySchema, MissionID: "survey", MissionRevision: "r1", Basis: x.Basis{WorldID: "warehouse", FrameID: "map", TransformRevision: "t1", MapID: "grid", MapRevision: "v1"}, GeometryID: "sha256:grid", TargetIDs: []string{"cell1", "cell2", "cell3"}, MaxAgeMS: 60000, Limit: 1000}
}
func coverageRequest(id string, targets ...string) x.Request {
	q := coverageQuery()
	raw, _ := json.Marshal(x.CoverageReport{SchemaVersion: x.CoverageSchema, MissionID: q.MissionID, MissionRevision: q.MissionRevision, GeometryID: q.GeometryID, ObservedAtMS: testNow.UnixMilli(), ObservedTargetIDs: targets})
	return x.Request{RequestID: id, PeerID: identityB.AgentID, Stream: "coverage", Kind: "observation", TTLMS: 60000, Basis: &q.Basis, Payload: raw}
}
func TestCoverageViewConsumesOnlyScopedFreshPeerReports(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 50)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b"), identityB, identityA, 50)
	defer bs.Close()
	req := coverageRequest("one", "cell1")
	e, err := a.Publish(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = b.Receive(ctx, identityA.AgentID, e); err != nil {
		t.Fatal(err)
	}
	view, err := b.CoverageView(ctx, coverageQuery())
	if err != nil || len(view.ProposedCoveredTargetIDs) != 1 || len(view.RemainingTargetIDs) != 2 || !view.AdvisoryOnly || view.GrantsExecution || view.PhysicalCompletion || !view.CompleteRead {
		t.Fatalf("view=%+v err=%v", view, err)
	}
	// These are consumer-side mismatches: authenticated receipts remain durable.
	for _, tc := range []struct {
		name   string
		change func(*x.CoverageQuery)
	}{
		{"mission", func(q *x.CoverageQuery) { q.MissionID = "different" }},
		{"revision", func(q *x.CoverageQuery) { q.MissionRevision = "r2" }},
		{"frame", func(q *x.CoverageQuery) { q.Basis.FrameID = "odom" }},
		{"transform", func(q *x.CoverageQuery) { q.Basis.TransformRevision = "t2" }},
		{"map", func(q *x.CoverageQuery) { q.Basis.MapRevision = "v2" }},
		{"geometry", func(q *x.CoverageQuery) { q.GeometryID = "other-grid" }},
		{"scope", func(q *x.CoverageQuery) { q.TargetIDs = []string{"cell2"} }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			q := coverageQuery()
			tc.change(&q)
			v, err := b.CoverageView(ctx, q)
			if err != nil || len(v.ProposedCoveredTargetIDs) != 0 || len(v.Rejected) != 1 {
				t.Fatalf("view=%+v err=%v", v, err)
			}
		})
	}
	b.now = func() time.Time { return testNow.Add(61 * time.Second) }
	view, err = b.CoverageView(ctx, coverageQuery())
	if err != nil || len(view.ProposedCoveredTargetIDs) != 0 || view.Rejected[0].Reason != "envelope_not_fresh" {
		t.Fatalf("expired=%+v %v", view, err)
	}
}

func TestCoverageViewRejectsInvalidNewerReportInsteadOfRevivingOldEvidence(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 50)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b"), identityB, identityA, 50)
	defer bs.Close()
	for _, r := range []x.Request{coverageRequest("one", "cell1"), coverageRequest("two", "outside-scope")} {
		e, err := a.Publish(ctx, r)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = b.Receive(ctx, identityA.AgentID, e); err != nil {
			t.Fatal(err)
		}
	}
	view, err := b.CoverageView(ctx, coverageQuery())
	if err != nil || len(view.ProposedCoveredTargetIDs) != 0 || len(view.Rejected) != 1 || view.Rejected[0].Reason != "target_outside_scope" {
		t.Fatalf("view=%+v err=%v", view, err)
	}
	q := coverageQuery()
	q.Limit = 1
	view, err = b.CoverageView(ctx, q)
	if err != nil || !view.CompleteRead || len(view.Contributions) != 0 || len(view.ProposedCoveredTargetIDs) != 0 || len(view.RemainingTargetIDs) != 3 {
		t.Fatalf("one current invalid stream=%+v err=%v", view, err)
	}
}

func TestDurableFeedPaginationRestartPruningAndNoOrdinalReuse(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	a, as := openService(t, filepath.Join(dir, "a"), identityA, identityB, 50)
	defer as.Close()
	b, bs := openService(t, filepath.Join(dir, "b"), identityB, identityA, 50)
	first := publish(t, a, "first")
	second := publish(t, a, "second")
	for _, e := range []x.Envelope{first, first, second} {
		if _, err := b.Receive(ctx, identityA.AgentID, e); err != nil {
			t.Fatal(err)
		}
	}
	feed, err := b.Feed(ctx, 0, 1)
	if err != nil || feed.Next != 1 || feed.Head != 2 || !feed.More {
		t.Fatalf("page=%+v err=%v", feed, err)
	}
	if err := bs.Close(); err != nil {
		t.Fatal(err)
	}
	b, bs = openService(t, filepath.Join(dir, "b"), identityB, identityA, 50)
	defer bs.Close()
	feed, err = b.Feed(ctx, feed.Next, 1)
	if err != nil || feed.Next != 2 || len(feed.Items) != 1 || feed.More || feed.Items[0].Envelope.MessageID != second.MessageID {
		t.Fatalf("restart=%+v %v", feed, err)
	}
	later := testNow.Add(25 * time.Hour)
	a.now = func() time.Time { return later }
	b.now = a.now
	if err := b.Prune(ctx); err != nil {
		t.Fatal(err)
	}
	feed, err = b.Feed(ctx, 0, 10)
	if !errors.Is(err, x.ErrResyncRequired) || feed.PrunedThrough != 2 || feed.Head != 2 {
		t.Fatalf("gap=%+v err=%v", feed, err)
	}
	third := publish(t, a, "third")
	if _, err := b.Receive(ctx, identityA.AgentID, third); err != nil {
		t.Fatal(err)
	}
	feed, err = b.Feed(ctx, 2, 10)
	if err != nil || feed.Next != 3 || len(feed.Items) != 1 || feed.Items[0].Revision != 3 {
		t.Fatalf("reuse=%+v err=%v", feed, err)
	}
	if _, err = b.Feed(ctx, 4, 10); err == nil {
		t.Fatal("accepted future cursor")
	}
	view, err := b.CoverageView(ctx, coverageQuery())
	if err != nil || view.Cursor != 3 || view.PrunedThrough != 2 {
		t.Fatalf("resync=%+v %v", view, err)
	}
}

func TestFeedAndCoverageAdminRequireAuthenticationAndStrictQueries(t *testing.T) {
	b, bs := openService(t, filepath.Join(t.TempDir(), "b"), identityB, identityA, 10)
	defer bs.Close()
	token := strings.Repeat("a", 32)
	handler, err := b.AdminHandler(token)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/v1/exchange/feed?after=0&limit=10", "/v1/exchange/coverage-view"} {
		r := httptest.NewRequest(http.MethodGet, path, nil)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 401 {
			t.Fatal(w.Code)
		}
	}
	for _, query := range []string{"after=0", "after=-1&limit=1", "after=0&limit=0", "after=0&limit=1001", "after=0&limit=1&extra=yes", "after=0&after=1&limit=1"} {
		r := httptest.NewRequest(http.MethodGet, "/v1/exchange/feed?"+query, nil)
		r.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 400 {
			t.Errorf("query %s got %d", query, w.Code)
		}
	}
	raw, _ := json.Marshal(coverageQuery())
	r := httptest.NewRequest(http.MethodPost, "/v1/exchange/coverage-view", strings.NewReader(string(raw)))
	r.Header.Set("Authorization", "Bearer "+token)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	if w.Code != 200 {
		t.Fatalf("%d %s", w.Code, w.Body.String())
	}
}
