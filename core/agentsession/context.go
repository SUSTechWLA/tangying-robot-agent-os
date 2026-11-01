// Package agentsession carries a host-owned planning session binding. It is not
// an authentication token, an execution lease, or permission to repeat a task.
package agentsession

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"unicode/utf8"
)

// Binding is an immutable snapshot for one intake turn. Context is historical
// reference material; the original user request remains a separate input.
type Binding struct {
	SessionID     string `json:"sessionId"`
	TurnID        string `json:"turnId"`
	TaskID        string `json:"taskId"`
	RobotID       string `json:"robotId"`
	Adapter       string `json:"adapter"`
	Sequence      uint64 `json:"sequence"`
	Context       string `json:"context"`
	ContextSHA256 string `json:"contextSHA256"`
}

type bindingKey struct{}

func WithBinding(ctx context.Context, binding Binding) context.Context {
	return context.WithValue(ctx, bindingKey{}, binding)
}

func FromContext(ctx context.Context) (Binding, bool) {
	binding, ok := ctx.Value(bindingKey{}).(Binding)
	return binding, ok
}

// Validate rejects corrupted persisted or accidentally forged context snapshots.
// This is an integrity check only: possession of a binding conveys no authority.
func (b Binding) Validate() error {
	if b.SessionID == "" || b.TurnID == "" || b.TaskID == "" || b.RobotID == "" || b.Adapter == "" || b.Sequence == 0 {
		return errors.New("incomplete session binding")
	}
	for _, id := range []string{b.SessionID, b.TurnID, b.TaskID, b.RobotID, b.Adapter} {
		if len(id) > 256 || !utf8.ValidString(id) {
			return errors.New("invalid session identity")
		}
	}
	if len(b.Context) > 6000 || !utf8.ValidString(b.Context) {
		return errors.New("invalid or oversized session context")
	}
	digest := sha256.Sum256([]byte(b.Context))
	if hex.EncodeToString(digest[:]) != b.ContextSHA256 {
		return errors.New("session context hash mismatch")
	}
	return nil
}

// HistoryRecord retains an original source request outside Binding.Context.
// Consumers may spill it behind their normal bounded context-read mechanism.
// It remains historical input, never a fresh goal or authorization.
type HistoryRecord struct {
	TurnID        string `json:"turnId"`
	TaskID        string `json:"taskId"`
	Request       string `json:"request"`
	RequestSHA256 string `json:"requestSHA256"`
}
type historyKey struct{}

func WithHistory(ctx context.Context, records []HistoryRecord) context.Context {
	return context.WithValue(ctx, historyKey{}, append([]HistoryRecord(nil), records...))
}
func HistoryFromContext(ctx context.Context) []HistoryRecord {
	records, _ := ctx.Value(historyKey{}).([]HistoryRecord)
	return append([]HistoryRecord(nil), records...)
}
