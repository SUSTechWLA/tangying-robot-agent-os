package agentsession

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"unicode/utf8"

	session "github.com/SUSTechWLA/tangying-robot-agent-os/core/agentsession"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func openManager(t *testing.T) (*Manager, *sqlite.Store) {
	t.Helper()
	s, err := sqlite.Open(filepath.Join(t.TempDir(), "sessions.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	m, err := New(s, s, "robot-a")
	if err != nil {
		t.Fatal(err)
	}
	return m, s
}
func begin(t *testing.T, m *Manager, request, adapter string) (context.Context, session.Binding) {
	t.Helper()
	ctx, err := m.Begin(context.Background(), request, adapter)
	if err != nil {
		t.Fatal(err)
	}
	b, ok := session.FromContext(ctx)
	if !ok {
		t.Fatal("binding absent")
	}
	return ctx, b
}
func create(t *testing.T, s *sqlite.Store, b session.Binding, state taskgraph.TaskState) {
	t.Helper()
	now := time.Now().UTC()
	err := s.Create(context.Background(), &tasks.Task{ID: b.TaskID, Request: "original request", Adapter: b.Adapter, State: state, CurrentRevision: 1, AggregateVersion: 1, CreatedAt: now, UpdatedAt: now, Events: []tasks.TaskEvent{{Sequence: 1, Type: "TASK_CREATED", Payload: map[string]any{"session": b}, OccurredAt: now}}})
	if err != nil {
		t.Fatal(err)
	}
}

func TestDurableTurnAndCurrentTaskFacts(t *testing.T) {
	m, s := openManager(t)
	ctx, b := begin(t, m, "检查厨房地图", "gazebo")
	if b.Sequence != 1 || b.ContextSHA256 != hash(b.Context) {
		t.Fatal(b)
	}
	create(t, s, b, taskgraph.StateReady)
	if err := m.Complete(ctx, b.TaskID, nil); err != nil {
		t.Fatal(err)
	}
	task, err := s.Get(context.Background(), b.TaskID)
	if err != nil {
		t.Fatal(err)
	}
	task.State = taskgraph.TaskState("UNKNOWN")
	if err = s.Update(context.Background(), task); err != nil {
		t.Fatal(err)
	}
	next, b2 := begin(t, m, "现在只查看进展", "gazebo")
	defer m.Complete(next, "", errors.New("fixture"))
	if b2.SessionID != b.SessionID || b2.Sequence != 2 || !strings.Contains(b2.Context, "UNKNOWN") || !strings.Contains(b2.Context, b.TaskID) || !strings.Contains(b2.Context, "非权威") {
		t.Fatal(b2)
	}
	events, err := s.ListEvents(context.Background(), "intake_session", m.key("gazebo"), 0, 20)
	if err != nil || len(events) != 3 {
		t.Fatalf("events=%d err=%v", len(events), err)
	}
	if events[0].Payload["request"] != "检查厨房地图" {
		t.Fatal("original request lost")
	}
}

func TestReopenReconcilesCommittedTaskWithoutReplay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "restart.db")
	s, err := sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	m, _ := New(s, s, "robot-a")
	_, b := begin(t, m, "巡检", "gazebo")
	create(t, s, b, taskgraph.StateReady)
	// Crash after Create but before Complete. No completion is run on the old owner.
	if err = s.Close(); err != nil {
		t.Fatal(err)
	}
	s, err = sqlite.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	reopened, _ := New(s, s, "robot-a")
	next, b2 := begin(t, reopened, "查看结果", "gazebo")
	defer reopened.Complete(next, "", errors.New("fixture"))
	if b2.SessionID != b.SessionID || !strings.Contains(b2.Context, b.TaskID) || !strings.Contains(b2.Context, "task_created") {
		t.Fatal(b2)
	}
	all, err := s.List(context.Background())
	if err != nil || len(all) != 1 {
		t.Fatalf("task replayed: %d %v", len(all), err)
	}
	events, _ := s.ListEvents(context.Background(), "intake_session", reopened.key("gazebo"), 0, 20)
	if events[1].EventType != "SESSION_TURN_RECOVERED" {
		t.Fatal(events)
	}
}

func TestReopenMarksMissingTaskInterrupted(t *testing.T) {
	m, s := openManager(t)
	_, b := begin(t, m, "previous incomplete planning", "gazebo")
	reopened, _ := New(s, s, "robot-a")
	ctx, next := begin(t, reopened, "new explicit input", "gazebo")
	defer reopened.Complete(ctx, "", errors.New("fixture"))
	if !strings.Contains(next.Context, "interrupted") || !strings.Contains(next.Context, "no automatic replay") || !strings.Contains(next.Context, b.TurnID) {
		t.Fatal(next.Context)
	}
	all, err := s.List(context.Background())
	if err != nil || len(all) != 0 {
		t.Fatal("recovery created a task", err)
	}
}

func TestCancelledWaitAndCancelledFinalization(t *testing.T) {
	m, s := openManager(t)
	ctx, b := begin(t, m, "first", "gazebo")
	waiting, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, err := m.Begin(waiting, "second", "gazebo"); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	cancelled, stop := context.WithCancel(ctx)
	stop()
	if err := m.Complete(cancelled, "", context.Canceled); err != nil {
		t.Fatal(err)
	}
	events, _ := s.ListEvents(context.Background(), "intake_session", m.key("gazebo"), 0, 20)
	if len(events) != 2 {
		t.Fatal(len(events))
	}
	next, b2 := begin(t, m, "third", "gazebo")
	defer m.Complete(next, "", errors.New("fixture"))
	if b2.Sequence != 2 || !strings.Contains(b2.Context, b.TurnID) || !strings.Contains(b2.Context, "context canceled") {
		t.Fatal(b2)
	}
}

func TestCompletionOwnershipDoesNotReleaseOtherTurn(t *testing.T) {
	m, _ := openManager(t)
	ctx, b := begin(t, m, "first", "gazebo")
	if err := m.Complete(session.WithBinding(context.Background(), b), b.TaskID, nil); err == nil {
		t.Fatal("forged completion accepted")
	}
	waiting, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, err := m.Begin(waiting, "still blocked", "gazebo"); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	if err := m.Complete(ctx, "", errors.New("fixture")); err != nil {
		t.Fatal(err)
	}
	if err := m.Complete(ctx, "", nil); err == nil {
		t.Fatal("duplicate completion accepted")
	}
	next, _ := begin(t, m, "next", "gazebo")
	m.Complete(next, "", errors.New("fixture"))
}

func TestRobotAdapterIsolationAndRotation(t *testing.T) {
	m, s := openManager(t)
	now := time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)
	m.now = func() time.Time { return now }
	ctx, b := begin(t, m, "secret robot-a gazebo request", "gazebo")
	m.Complete(ctx, "", errors.New("fixture"))
	ctx, ros := begin(t, m, "ros input", "ros2")
	if ros.SessionID == b.SessionID || strings.Contains(ros.Context, "secret robot-a") {
		t.Fatal("adapter leak")
	}
	m.Complete(ctx, "", errors.New("fixture"))
	other, _ := New(s, s, "robot-b")
	ctx, b3 := begin(t, other, "other robot", "gazebo")
	if b3.SessionID == b.SessionID || strings.Contains(b3.Context, "secret robot-a") {
		t.Fatal("robot leak")
	}
	other.Complete(ctx, "", errors.New("fixture"))
	now = now.Add(idleWindow)
	ctx, rotated := begin(t, m, "after idle", "gazebo")
	if rotated.SessionID == b.SessionID || rotated.Sequence != 1 || strings.Contains(rotated.Context, "secret robot-a") {
		t.Fatal("idle did not rotate")
	}
	m.Complete(ctx, "", errors.New("fixture"))
	first := rotated.SessionID
	for i := 0; i < maxTurns; i++ {
		ctx, next := begin(t, m, "bounded turn", "gazebo")
		if i == maxTurns-1 && (next.SessionID == first || next.Sequence != 1) {
			t.Fatal("turn budget did not rotate")
		}
		if err := m.Complete(ctx, "", errors.New("fixture")); err != nil {
			t.Fatal(err)
		}
	}
}

type failingStore struct {
	eventstore.Store
	fail atomic.Bool
}

func (s *failingStore) Commit(ctx context.Context, r eventstore.CommitRequest) error {
	if s.fail.Load() {
		return errors.New("injected commit failure")
	}
	return s.Store.Commit(ctx, r)
}
func TestFinalizeFailureAndAmbiguousCreateRecoverByID(t *testing.T) {
	_, s := openManager(t)
	store := &failingStore{Store: s}
	m, _ := New(store, s, "robot-a")
	ctx, b := begin(t, m, "first", "gazebo")
	create(t, s, b, taskgraph.StateReady)
	store.fail.Store(true)
	if err := m.Complete(ctx, b.TaskID, nil); err == nil {
		t.Fatal("missing failure")
	}
	store.fail.Store(false)
	next, b2 := begin(t, m, "second", "gazebo")
	if !strings.Contains(b2.Context, b.TaskID) || !strings.Contains(b2.Context, "task_created") {
		t.Fatal(b2.Context)
	}
	create(t, s, b2, taskgraph.StateReady)
	// Writer committed but the caller lost its success response.
	if err := m.Complete(next, "", errors.New("ambiguous persistence reply")); err != nil {
		t.Fatal(err)
	}
	last, b3 := begin(t, m, "third", "gazebo")
	defer m.Complete(last, "", errors.New("fixture"))
	if strings.Contains(b3.Context, "intake_failed") {
		t.Fatal(b3.Context)
	}
}

func TestBeginCommitFailureDoesNotStartPlanningOrHoldGate(t *testing.T) {
	_, s := openManager(t)
	store := &failingStore{Store: s}
	m, _ := New(store, s, "robot-a")
	store.fail.Store(true)
	if ctx, err := m.Begin(context.Background(), "not received", "gazebo"); err == nil || ctx != nil {
		t.Fatal("failure admitted")
	}
	store.fail.Store(false)
	ctx, b := begin(t, m, "first durable request", "gazebo")
	defer m.Complete(ctx, "", errors.New("fixture"))
	if b.Sequence != 1 {
		t.Fatal(b)
	}
}

func TestBoundedUTF8ProjectionAndRetainedOriginal(t *testing.T) {
	m, s := openManager(t)
	request := strings.Repeat("中文指令", 600)
	for i := 0; i < 15; i++ {
		ctx, b := begin(t, m, request, "gazebo")
		if !utf8.ValidString(b.Context) || len(b.Context) > maxContextBytes {
			t.Fatal(len(b.Context))
		}
		if err := m.Complete(ctx, "", errors.New(strings.Repeat("原因", 1000))); err != nil {
			t.Fatal(err)
		}
	}
	st, _, err := m.load(context.Background(), m.key("gazebo"), "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	if len(st.Recent) != retainedTurns {
		t.Fatal(len(st.Recent))
	}
	for _, row := range st.Recent {
		if len(row.RequestPreview) > 512 || !utf8.ValidString(row.RequestPreview) || len(row.ErrorPreview) > 512 {
			t.Fatal(row)
		}
	}
	events, err := s.ListEvents(context.Background(), "intake_session", m.key("gazebo"), 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 30 || events[0].Payload["request"] != request {
		t.Fatal("raw journal was compressed")
	}
}

func TestMismatchedPersistedTaskFailsClosed(t *testing.T) {
	m, s := openManager(t)
	ctx, b := begin(t, m, "first", "gazebo")
	wrong := b
	wrong.RobotID = "other-robot"
	create(t, s, wrong, taskgraph.StateReady)
	if err := m.Complete(ctx, b.TaskID, nil); err == nil {
		t.Fatal("mismatched binding accepted")
	}
	if ctx, err := m.Begin(context.Background(), "new", "gazebo"); err == nil || ctx != nil {
		t.Fatal("mismatch silently forgotten")
	}
}

func TestFullHistorySeparateFromBoundedProjection(t *testing.T) {
	m, _ := openManager(t)
	request := strings.Repeat("历史中文说明", 400) + "完整原文结尾"
	ctx, b := begin(t, m, request, "gazebo")
	if err := m.Complete(ctx, "", errors.New("fixture")); err != nil {
		t.Fatal(err)
	}
	next, b2 := begin(t, m, "只查看当前状态", "gazebo")
	defer m.Complete(next, "", errors.New("fixture"))
	history := session.HistoryFromContext(next)
	if len(history) != 1 || history[0].Request != request || history[0].RequestSHA256 != hash(request) || history[0].TurnID != b.TurnID || history[0].TaskID != b.TaskID {
		t.Fatal("full source was not recalled")
	}
	if strings.Contains(b2.Context, "完整原文结尾") {
		t.Fatal("full history recurred inside compact projection")
	}
}

type corruptHistoryStore struct {
	eventstore.Store
	corrupt bool
}

func (s *corruptHistoryStore) ListEvents(ctx context.Context, kind, key string, after uint64, limit int) ([]eventstore.DomainEvent, error) {
	rows, err := s.Store.ListEvents(ctx, kind, key, after, limit)
	if s.corrupt && err == nil && len(rows) > 0 {
		rows[0].Payload["request"] = "different content"
	}
	return rows, err
}
func TestSourceDigestMismatchStopsBeforeReceivingNewTurn(t *testing.T) {
	_, s := openManager(t)
	store := &corruptHistoryStore{Store: s}
	m, _ := New(store, s, "robot-a")
	ctx, _ := begin(t, m, "original history", "gazebo")
	if err := m.Complete(ctx, "", errors.New("fixture")); err != nil {
		t.Fatal(err)
	}
	store.corrupt = true
	if ctx, err := m.Begin(context.Background(), "new input", "gazebo"); err == nil || ctx != nil {
		t.Fatal("corrupt history admitted")
	}
	rows, _ := s.ListEvents(context.Background(), "intake_session", m.key("gazebo"), 0, 10)
	if len(rows) != 2 {
		t.Fatal("new turn incorrectly received")
	}
	store.corrupt = false
	ctx, _ = begin(t, m, "after repair", "gazebo")
	m.Complete(ctx, "", errors.New("fixture"))
}

func TestConcurrentInputsSerializeAndKeepBothTurns(t *testing.T) {
	m, s := openManager(t)
	first, b := begin(t, m, "first", "gazebo")
	started := make(chan struct{})
	result := make(chan error, 1)
	go func() {
		close(started)
		ctx, err := m.Begin(context.Background(), "second", "gazebo")
		if err != nil {
			result <- err
			return
		}
		second, _ := session.FromContext(ctx)
		if second.SessionID != b.SessionID || second.Sequence != 2 {
			result <- errors.New("turn ordering lost")
			m.Complete(ctx, "", errors.New("fixture"))
			return
		}
		result <- m.Complete(ctx, "", errors.New("fixture"))
	}()
	<-started
	select {
	case err := <-result:
		t.Fatalf("second entered before first ended: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	if err := m.Complete(first, "", errors.New("fixture")); err != nil {
		t.Fatal(err)
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	events, err := s.ListEvents(context.Background(), "intake_session", m.key("gazebo"), 0, 10)
	if err != nil || len(events) != 4 {
		t.Fatal(len(events), err)
	}
}
