package capabilityagent

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/grpc/status"
)

// A transport request is a separate atomic attempt from its parent capability.
// In particular status polls, verification reads and retry attempts must not
// overwrite each other merely because they share a command/request identity.
type rpcPhaseKey struct{}

func withRPCPhase(ctx context.Context, phase string) context.Context {
	return context.WithValue(ctx, rpcPhaseKey{}, phase)
}

var errRPCNotDispatched = errors.New("RPC not dispatched because start evidence could not be persisted")

// rpcEvidenceError keeps persistence and provider failures independently
// inspectable. A successful transport with missing evidence is not success.
type rpcEvidenceError struct {
	evidence, provider error
}

func (e *rpcEvidenceError) Error() string {
	return errors.Join(fmt.Errorf("RPC evidence persistence: %w", e.evidence), e.provider).Error()
}

func (e *rpcEvidenceError) Unwrap() []error {
	if e.provider == nil {
		return []error{e.evidence}
	}
	return []error{e.evidence, e.provider}
}

// auditValue keeps bulk observations out of task snapshots while recording
// exactly what was omitted. Hashes describe the canonical JSON received by this
// process, not a claim that omitted bytes are recoverable from an artifact.
func auditValue(payload map[string]any, name string, value map[string]any) {
	wire, err := json.Marshal(value)
	if err != nil {
		payload[name+"EncodingError"] = err.Error()
		return
	}
	digest := sha256.Sum256(wire)
	payload[name+"SHA256"], payload[name+"Bytes"] = hex.EncodeToString(digest[:]), len(wire)
	projected := project(value)
	preview, err := json.Marshal(projected)
	if err != nil {
		payload[name+"EncodingError"] = err.Error()
		return
	}
	payload[name+"Projected"] = string(preview) != string(wire)
	payload[name+"Omitted"] = len(preview) > 16*1024
	if len(preview) <= 16*1024 {
		payload[name] = projected
	}
}

// Provider prose is not a trusted size boundary. Keep a UTF-8-safe preview and
// a digest of the original bytes; do not mutate the provider error used by the
// execution policy. Projected also flags replacement of invalid UTF-8 bytes.
func auditText(payload map[string]any, name, value string, limit int) {
	digest := sha256.Sum256([]byte(value))
	payload[name+"SHA256"], payload[name+"Bytes"] = hex.EncodeToString(digest[:]), len(value)
	preview := strings.ToValidUTF8(value, "\uFFFD")
	payload[name+"Truncated"] = len(preview) > limit
	if len(preview) > limit {
		end := limit
		for end > 0 && !utf8.ValidString(preview[:end]) {
			end--
		}
		preview = preview[:end]
	}
	payload[name], payload[name+"Projected"] = preview, preview != value
}

func (e *Executor) callTraced(ctx context.Context, request *robotv1.ServiceRequest) (map[string]any, error) {
	binding, traced := ctx.Value(executionEventBindingKey{}).(executionEventBinding)
	traced = traced && e.Tasks != nil
	phase, _ := ctx.Value(rpcPhaseKey{}).(string)
	if phase == "" {
		phase = "dispatch"
	}
	started := time.Now()
	payload := map[string]any{
		"rpcId": rand.Text(), "service": request.Name, "requestId": request.RequestId,
		"phase": phase, "status": "STARTED", "operationId": request.OperationId,
		"operationOwnerId": request.OperationOwnerId, "operationLeaseMs": request.OperationLeaseMs,
		"startedAt": started.UTC().Format(time.RFC3339Nano),
	}
	var evidenceErr error
	if traced {
		auditValue(payload, "arguments", request.Parameters.AsMap())
		evidenceErr = e.record(ctx, binding.taskID, binding.stepID, "CAPABILITY_RPC_STARTED", payload)
		if evidenceErr != nil {
			if _, stopping := capability.OwnedStopFromContext(ctx); !stopping {
				return nil, errors.Join(errRPCNotDispatched, evidenceErr)
			}
			// An unavailable audit disk must not veto the already-owned physical
			// stop. The caller still receives the evidence failure below.
		}
	}
	dispatched := time.Now()
	response, providerErr := e.Provider.CallService(ctx, request)
	providerDuration := time.Since(dispatched)
	var result map[string]any
	if response != nil {
		result = response.Result.AsMap()
		auditText(payload, "providerCode", response.Code, 256)
		auditText(payload, "providerMessage", response.Message, 2048)
	}
	if providerErr == nil {
		if response == nil {
			providerErr = fmt.Errorf("empty service response")
		} else if !response.Ok {
			providerErr = &ServiceError{Code: response.Code, Message: response.Message, Rejected: result["outcome"] == "REJECTED"}
		}
	}
	if traced {
		finished := time.Now()
		payload["finishedAt"], payload["durationMs"] = finished.UTC().Format(time.RFC3339Nano), finished.Sub(started).Milliseconds()
		// durationMs includes the write-ahead audit; providerDurationMs measures
		// only CallService, so audit overhead is not mistaken for robot latency.
		payload["providerDurationMs"] = providerDuration.Milliseconds()
		payload["status"] = "SUCCEEDED"
		payload["startPersisted"] = evidenceErr == nil
		auditValue(payload, "result", result)
		if providerErr != nil {
			payload["status"], payload["transportCode"] = "FAILED", status.Code(providerErr).String()
			auditText(payload, "error", providerErr.Error(), 2048)
		}
		// Caller cancellation cannot erase the result of a dispatched request.
		persistCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
		finishErr := e.record(persistCtx, binding.taskID, binding.stepID, "CAPABILITY_RPC_FINISHED", payload)
		cancel()
		evidenceErr = errors.Join(evidenceErr, finishErr)
	}
	if evidenceErr != nil {
		return result, &rpcEvidenceError{evidence: evidenceErr, provider: providerErr}
	}
	return result, providerErr
}
