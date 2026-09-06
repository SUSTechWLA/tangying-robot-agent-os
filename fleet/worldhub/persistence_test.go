package worldhub

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

func openPersistentTestHub(t *testing.T, path string) (*Hub, *FileStore) {
	t.Helper()
	store, err := OpenFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	hub, err := NewPersistent(context.Background(), "world-test", time.Minute, 4, store)
	if err != nil {
		store.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return hub, store
}

func TestPersistentHubRecoversRevisionDeduplicationAndStaleEvidence(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "world.json")
	hub, store := openPersistentTestHub(t, path)
	event := validObservation(7, "original")
	event.ObservedAt, event.ReceivedAt = time.Now().UTC(), time.Now().UTC()
	if _, err := hub.Ingest(ctx, event); err != nil {
		t.Fatal(err)
	}
	fixture := staticObservation(8, "fixture")
	fixture.ObservedAt, fixture.ReceivedAt = event.ObservedAt, event.ReceivedAt
	if _, err := hub.Ingest(ctx, fixture); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	recovered, _ := openPersistentTestHub(t, path)
	snapshot, err := recovered.Snapshot(ctx)
	if err != nil || snapshot.Revision != 2 || snapshot.EventCursor != "fixture" || snapshot.Entities["red-block"].Pose[0] != 7 {
		t.Fatalf("recovered=%#v err=%v", snapshot, err)
	}
	if snapshot.Entities["red-block"].Freshness != worldmodel.Stale || snapshot.Entities["fixture"].Freshness != worldmodel.Stale || snapshot.Sources[event.SourceID].Freshness != worldmodel.Stale {
		t.Fatalf("recovered evidence was trusted as fresh: %#v", snapshot)
	}
	if _, err := recovered.Subscribe(ctx, 1); !errors.Is(err, worldmodel.ErrResyncRequired) {
		t.Fatalf("missing replay must require resync: %v", err)
	}
	duplicate := event
	duplicate.ObservationID = "new-id-old-sequence"
	if delta, err := recovered.Ingest(ctx, duplicate); err != nil || delta.Revision != 0 {
		t.Fatalf("old sequence accepted: %#v %v", delta, err)
	}
	duplicate.SourceSequence = 9
	duplicate.ObservationID = "original"
	if delta, err := recovered.Ingest(ctx, duplicate); err != nil || delta.Revision != 0 {
		t.Fatalf("old observation ID accepted: %#v %v", delta, err)
	}
	fixture.SourceSequence, fixture.ObservationID = 9, "fresh-fixture"
	fixture.ObservedAt, fixture.ReceivedAt = time.Now().UTC(), time.Now().UTC()
	if delta, err := recovered.Ingest(ctx, fixture); err != nil || delta.Revision != 3 || delta.Snapshot.Entities["fixture"].Freshness != worldmodel.Fresh || delta.Snapshot.Entities["red-block"].Freshness != worldmodel.Stale {
		t.Fatalf("fresh fixture must refresh only its own evidence: %#v %v", delta, err)
	}
}

func TestPersistentHubRetainsSuppressedStaticSourceHighWater(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "world.json")
	hub, store := openPersistentTestHub(t, path)
	for _, event := range []observation.Envelope{staticObservation(1, "static-1"), staticObservation(5, "static-5")} {
		if _, err := hub.Ingest(ctx, event); err != nil {
			t.Fatal(err)
		}
	}
	if snapshot, _ := hub.Snapshot(ctx); snapshot.Revision != 1 || snapshot.Sources["robot-1/sim"].SourceSequence != 1 {
		t.Fatalf("suppression changed public state: %#v", snapshot)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	recovered, _ := openPersistentTestHub(t, path)
	if delta, err := recovered.Ingest(ctx, validObservation(4, "late-dynamic")); err != nil || delta.Revision != 0 {
		t.Fatalf("suppressed high-water lost: %#v %v", delta, err)
	}
	if delta, err := recovered.Ingest(ctx, validObservation(6, "next-dynamic")); err != nil || delta.Revision != 2 {
		t.Fatalf("revision continuity lost: %#v %v", delta, err)
	}
}

type failingCheckpointStore struct {
	SnapshotStore
	failure     error
	afterCommit bool
	afterSave   func()
}

func (s *failingCheckpointStore) Save(ctx context.Context, checkpoint worldmodel.Checkpoint) error {
	if s.failure != nil && !s.afterCommit {
		return s.failure
	}
	if err := s.SnapshotStore.Save(ctx, checkpoint); err != nil {
		return err
	}
	if s.afterSave != nil {
		s.afterSave()
	}
	return s.failure
}

func TestPersistentHubNeverPublishesFailedCheckpointAndRequiresRestart(t *testing.T) {
	for _, afterCommit := range []bool{false, true} {
		t.Run(fmt.Sprint(afterCommit), func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			path := filepath.Join(t.TempDir(), "world.json")
			store, err := OpenFileStore(path)
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			fault := &failingCheckpointStore{SnapshotStore: store, afterCommit: afterCommit}
			hub, err := NewPersistent(ctx, "world-test", time.Minute, 4, fault)
			if err != nil {
				t.Fatal(err)
			}
			updates, err := hub.Subscribe(ctx, 0)
			if err != nil {
				t.Fatal(err)
			}
			fault.failure = errors.New("disk sync failed")
			if delta, err := hub.Ingest(ctx, validObservation(1, "failed")); err == nil || delta.Revision != 0 {
				t.Fatalf("failed save acknowledged: %#v %v", delta, err)
			}
			select {
			case delta, ok := <-updates:
				if ok {
					t.Fatalf("unpersisted update published: %#v", delta)
				}
			default:
				t.Fatal("failed persistent stream must close")
			}
			if _, err := hub.Snapshot(ctx); err == nil {
				t.Fatal("ambiguous persistence must fail closed for readers")
			}
			fault.failure = nil
			if _, err := hub.Ingest(ctx, validObservation(2, "next")); err == nil {
				t.Fatal("failed hub resumed without recovery")
			}
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			recovered, _ := openPersistentTestHub(t, path)
			snapshot, err := recovered.Snapshot(ctx)
			want := uint64(0)
			if afterCommit {
				want = 1
			}
			if err != nil || snapshot.Revision != want {
				t.Fatalf("recovery revision=%d want=%d err=%v", snapshot.Revision, want, err)
			}
		})
	}
}

func TestPersistentHubSerializesConcurrentDurableRevisions(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "world.json")
	hub, store := openPersistentTestHub(t, path)
	var workers sync.WaitGroup
	for i := 0; i < 24; i++ {
		workers.Add(1)
		go func(i int) {
			defer workers.Done()
			event := validObservation(1, fmt.Sprintf("event-%d", i))
			event.SourceID = fmt.Sprintf("source-%d", i)
			if _, err := hub.Ingest(ctx, event); err != nil {
				t.Error(err)
			}
		}(i)
	}
	workers.Wait()
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	recovered, _ := openPersistentTestHub(t, path)
	snapshot, err := recovered.Snapshot(ctx)
	if err != nil || snapshot.Revision != 24 || len(snapshot.Sources) != 24 {
		t.Fatalf("lost concurrent revision: %#v %v", snapshot, err)
	}
}

func TestPersistentHubRejectsCorruptAndWrongWorldCheckpoints(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "world.json")
	hub, store := openPersistentTestHub(t, path)
	if _, err := hub.Ingest(ctx, validObservation(1, "one")); err != nil {
		t.Fatal(err)
	}
	if _, err := NewPersistent(ctx, "another-world", time.Minute, 4, store); err == nil {
		t.Fatal("wrong world accepted")
	}
	if err := os.WriteFile(path, []byte(`{"schemaVersion":"world.checkpoint.file.v1","checkpoint":{}}`), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := NewPersistent(ctx, "world-test", time.Minute, 4, store); err == nil {
		t.Fatal("corrupt checkpoint silently reset")
	}
}

func TestFileStoreExcludesAnotherWriterAndReleasesLock(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	first, err := OpenFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := OpenFileStore(path); err == nil {
		second.Close()
		t.Fatal("same checkpoint admitted two writers")
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second, err := OpenFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	second.Close()
}

func TestPersistentHubSurvivesAbruptProcessExit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	command := exec.Command(os.Args[0], "-test.run=^TestWorldCheckpointCrashProcess$")
	command.Env = append(os.Environ(), "WORLD_CHECKPOINT_CRASH_PATH="+path)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("checkpoint child: %v %s", err, output)
	}
	// An incomplete temporary file from a killed write is never a checkpoint.
	if err := os.WriteFile(filepath.Join(filepath.Dir(path), ".world.json.tmp-crash"), []byte("partial"), 0600); err != nil {
		t.Fatal(err)
	}
	recovered, _ := openPersistentTestHub(t, path)
	snapshot, err := recovered.Snapshot(context.Background())
	if err != nil || snapshot.Revision != 1 || snapshot.EventCursor != "before-crash" || snapshot.Entities["red-block"].Freshness != worldmodel.Stale {
		t.Fatalf("crash recovery=%#v err=%v", snapshot, err)
	}
}

func TestWorldCheckpointCrashProcess(t *testing.T) {
	path := os.Getenv("WORLD_CHECKPOINT_CRASH_PATH")
	if path == "" {
		t.Skip("subprocess helper")
	}
	store, err := OpenFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	hub, err := NewPersistent(context.Background(), "world-test", time.Minute, 4, store)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := hub.Ingest(context.Background(), validObservation(11, "before-crash")); err != nil {
		t.Fatal(err)
	}
	os.Exit(0) // No defers, store close, Hub shutdown, or application cleanup.
}

func TestFileStoreRejectsUnserializableCheckpointWithoutReplacingCommittedFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	hub, store := openPersistentTestHub(t, path)
	if _, err := hub.Ingest(context.Background(), validObservation(1, "committed")); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	checkpoint, err := store.Load(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	entity := checkpoint.Snapshot.Entities["red-block"]
	entity.Pose[0] = math.NaN()
	if err := store.Save(context.Background(), checkpoint); err == nil {
		t.Fatal("unserializable checkpoint accepted")
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("failed checkpoint replaced committed bytes")
	}
	checkpoint, err = store.Load(context.Background())
	if err != nil || checkpoint.Snapshot.Revision != 1 {
		t.Fatalf("committed checkpoint lost: %#v %v", checkpoint, err)
	}
}

func TestPersistentHubDerivesPublishedFreshnessAfterDurableCommit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	store, err := OpenFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	delayed := &failingCheckpointStore{SnapshotStore: store}
	hub, err := NewPersistent(context.Background(), "world-test", time.Second, 4, delayed)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	hub.projector.SetNow(func() time.Time { return now })
	event := validObservation(1, "expires-during-commit")
	event.ObservedAt, event.ReceivedAt = now, now
	delayed.afterSave = func() { now = now.Add(2 * time.Second) }
	delta, err := hub.Ingest(context.Background(), event)
	if err != nil || delta.Revision != 1 || delta.Snapshot.Entities["red-block"].Freshness != worldmodel.Stale {
		t.Fatalf("publication used pre-commit freshness: %#v %v", delta, err)
	}
}
