package agentsession

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
)

func TestBindingIntegrityAndContextIsolation(t *testing.T) {
	digest := sha256.Sum256([]byte("history"))
	b := Binding{SessionID: "session-a", TurnID: "turn-a", TaskID: "task-a", RobotID: "robot-a", Adapter: "gazebo", Sequence: 1, Context: "history", ContextSHA256: hex.EncodeToString(digest[:])}
	if err := b.Validate(); err != nil {
		t.Fatal(err)
	}
	ctx := WithBinding(context.Background(), b)
	got, ok := FromContext(ctx)
	if !ok || got != b {
		t.Fatal(got)
	}
	got.Context = "tampered"
	if err := got.Validate(); err == nil {
		t.Fatal("bad hash accepted")
	}
	got = b
	got.Context = strings.Repeat("x", 6001)
	digest = sha256.Sum256([]byte(got.Context))
	got.ContextSHA256 = hex.EncodeToString(digest[:])
	if err := got.Validate(); err == nil {
		t.Fatal("oversized binding accepted")
	}
	if _, ok := FromContext(context.Background()); ok {
		t.Fatal("cross-context binding leak")
	}
}

func TestHistoryIsCopied(t *testing.T) {
	input := []HistoryRecord{{TurnID: "turn-a", TaskID: "task-a", Request: "original"}}
	ctx := WithHistory(context.Background(), input)
	input[0].Request = "mutated input"
	first := HistoryFromContext(ctx)
	if first[0].Request != "original" {
		t.Fatal(first)
	}
	first[0].Request = "mutated output"
	if HistoryFromContext(ctx)[0].Request != "original" {
		t.Fatal("context is mutable")
	}
	if len(HistoryFromContext(context.Background())) != 0 {
		t.Fatal("history leaked")
	}
}
