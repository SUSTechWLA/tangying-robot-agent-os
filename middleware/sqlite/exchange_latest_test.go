package sqlite

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"

	x "github.com/SUSTechWLA/tangying-robot-agent-os/core/exchangecontract"
)

var snapshotSender = x.Identity{AgentID: "sender", RobotID: "uav", Domain: "warehouse"}
var snapshotRecipient = x.Identity{AgentID: "receiver", RobotID: "ugv", Domain: "warehouse"}

func snapshotEnvelope(t *testing.T, sequence uint64) x.Envelope {
	t.Helper()
	e := x.Envelope{SchemaVersion: x.Schema, Sender: snapshotSender, Recipient: snapshotRecipient,
		Stream: "status", Kind: "mission_status", Sequence: sequence, CreatedAtMS: 1800000000000,
		ExpiresAtMS: 1800000060000, Payload: json.RawMessage(`{"state":"observed"}`)}
	var err error
	e.MessageID, err = e.Digest()
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func TestLatestExchangeSnapshotBackfillsLegacyInboxAndSurvivesReopen(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "legacy.db")
	s, err := OpenExchange(path, 20)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Bind(ctx, snapshotRecipient); err != nil {
		t.Fatal(err)
	}
	for _, sequence := range []uint64{5, 9} {
		if _, err := s.Accept(ctx, snapshotEnvelope(t, sequence), 1800000000000); err != nil {
			t.Fatal(err)
		}
	}
	// Recreate a pre-feed journal: the inbox and permanent cursor already exist,
	// but there has never been a receipt feed or retention watermark. Opening it
	// must backfill ordinals without inventing or resetting sender sequences.
	if _, err := s.db.Exec(`DROP TABLE exchange_feed`); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	for reopen := 0; reopen < 2; reopen++ {
		s, err = OpenExchange(path, 20)
		if err != nil {
			t.Fatal(err)
		}
		if err := s.Bind(ctx, snapshotRecipient); err != nil {
			t.Fatal(err)
		}
		latest, err := s.LatestSnapshot(ctx, snapshotRecipient, []x.Identity{snapshotSender}, 1)
		if err != nil || latest.More || latest.Head != 2 || latest.Next != 2 || len(latest.Items) != 1 || latest.Items[0].Revision != 2 || latest.Items[0].Envelope.Sequence != 9 {
			t.Fatalf("reopen %d latest=%+v err=%v", reopen, latest, err)
		}
		history, err := s.Feed(ctx, 0, 10)
		if err != nil || history.More || len(history.Items) != 2 || history.Items[0].Envelope.Sequence != 5 || history.Items[1].Envelope.Sequence != 9 {
			t.Fatalf("migration changed history: %+v %v", history, err)
		}
		if err := s.Close(); err != nil {
			t.Fatal(err)
		}
	}
	s, err = OpenExchange(path, 20)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if _, err := s.Accept(ctx, snapshotEnvelope(t, 10), 1800000000000); err != nil {
		t.Fatal(err)
	}
	latest, err := s.LatestSnapshot(ctx, snapshotRecipient, []x.Identity{snapshotSender}, 1)
	if err != nil || latest.Head != 3 || len(latest.Items) != 1 || latest.Items[0].Revision != 3 || latest.Items[0].Envelope.Sequence != 10 {
		t.Fatalf("post-migration cursor/ordinal continuity: %+v %v", latest, err)
	}
}

func TestLatestExchangeSnapshotRejectsInvalidScopeAndLimits(t *testing.T) {
	s, err := OpenExchange(filepath.Join(t.TempDir(), "scope.db"), 10)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, tc := range []struct {
		name      string
		recipient x.Identity
		senders   []x.Identity
		limit     int
	}{
		{"zero-limit", snapshotRecipient, []x.Identity{snapshotSender}, 0},
		{"excess-limit", snapshotRecipient, []x.Identity{snapshotSender}, 1001},
		{"invalid-recipient", x.Identity{}, []x.Identity{snapshotSender}, 1},
		{"no-senders", snapshotRecipient, nil, 1},
		{"invalid-sender", snapshotRecipient, []x.Identity{{}}, 1},
		{"duplicate-sender", snapshotRecipient, []x.Identity{snapshotSender, snapshotSender}, 1},
		{"self-sender", snapshotRecipient, []x.Identity{snapshotRecipient}, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := s.LatestSnapshot(context.Background(), tc.recipient, tc.senders, tc.limit); err == nil {
				t.Fatal("invalid snapshot scope accepted")
			}
		})
	}
}

func TestLatestExchangeSnapshotKeepsHeadAndItemsInOneReadTransaction(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "concurrent.db")
	reader, err := OpenExchange(path, 100)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	writer, err := OpenExchange(path, 100)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	var envelopes []x.Envelope
	for sequence := uint64(1); sequence <= 40; sequence++ {
		envelopes = append(envelopes, snapshotEnvelope(t, sequence))
	}
	done := make(chan error, 1)
	go func() {
		for _, e := range envelopes {
			if _, err := writer.Accept(ctx, e, e.CreatedAtMS); err != nil {
				done <- err
				return
			}
		}
		done <- nil
	}()
	check := func() {
		t.Helper()
		feed, err := reader.LatestSnapshot(ctx, snapshotRecipient, []x.Identity{snapshotSender}, 1)
		if err != nil || feed.More || feed.Next != feed.Head {
			t.Fatalf("snapshot metadata=%+v %v", feed, err)
		}
		if feed.Head == 0 {
			if len(feed.Items) != 0 {
				t.Fatal("items appeared after the captured empty head")
			}
		} else if len(feed.Items) != 1 || feed.Items[0].Revision != feed.Head || feed.Items[0].Envelope.Sequence != feed.Head {
			t.Fatalf("current row came from a different database snapshot: %+v", feed)
		}
	}
	for i := 0; i < 40; i++ {
		check()
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	check()
}
