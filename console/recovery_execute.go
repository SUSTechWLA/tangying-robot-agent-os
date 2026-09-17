package console

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

// Approving a recovery step and running it.
//
// This is the endpoint the recovery channel was missing. Until now a plan was
// something an operator read: the agent proposed "load a saved map", the console
// displayed it, and the person did it by hand. Here the approval and the execution
// are the same act, because the click *is* the approval — there is no second
// signature to collect after somebody has already decided.
//
// # What the operator is approving
//
// One action from the catalog, by id. Not "whatever the model decides would help":
// the action names the tools it may call, and that list is the scope the execution
// runs under. An action outside the catalog cannot be approved because it cannot be
// named.
//
// # The trust boundary, stated plainly
//
// The local console has no authentication and binds to loopback; anything that can
// reach it can call this. That is the same boundary the task approval endpoint
// already has — a person at the laptop can approve work — and it is not widened
// here. What this endpoint does *not* do is execute anything without a person:
// there is no timer, no automatic retry and no path from a plan to a call that does
// not pass through this request.
type RecoveryExecutor interface {
	Execute(ctx context.Context, request recoveryexec.Request) (recoveryexec.Result, error)
}

// recoveryCatalogReader is what the console needs to refuse an action the catalog
// does not contain.
type recoveryCatalogReader interface {
	Lookup(id string) (agentruntime.RecoveryAction, bool)
}

// RecoveryExecutionTimeout bounds one approved action.
//
// It is long because a recovery action can drive a robot, and a timeout that fired
// mid-motion would report a failure for something still happening — the operator
// would then be looking at a robot that is moving while the console says it failed.
const RecoveryExecutionTimeout = 3 * time.Minute

type recoveryExecuteRequest struct {
	PlanID string `json:"planId"`
	TaskID string `json:"taskId"`
	// ActionID names a catalog entry. It is required: an execution request without
	// one would have to guess what to run.
	ActionID string `json:"actionId"`
}

func (s *Server) executeRecovery(w http.ResponseWriter, r *http.Request) {
	var body recoveryExecuteRequest
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_RECOVERY_REQUEST", "请求格式不正确")
		return
	}
	actionID := strings.TrimSpace(body.ActionID)
	if actionID == "" {
		writeError(w, http.StatusBadRequest, "RECOVERY_ACTION_REQUIRED",
			"需要指定要执行的动作 id；没有 id 就无从知道要执行什么。")
		return
	}
	executor, ok := s.executor.(RecoveryExecutor)
	if !ok || executor == nil {
		writeError(w, http.StatusServiceUnavailable, "RECOVERY_EXECUTION_UNAVAILABLE",
			"这个部署没有配置恢复执行能力。")
		return
	}
	catalog, ok := s.executor.(recoveryCatalogReader)
	if !ok || catalog == nil {
		writeError(w, http.StatusServiceUnavailable, "RECOVERY_CATALOG_UNAVAILABLE",
			"读不到恢复动作目录，无法确认这个动作是否允许执行。")
		return
	}
	// The catalog is the boundary. An action not in it is not refused by the
	// executor later — it never gets that far, because approving something the
	// system has no entry for is approving nothing.
	action, known := catalog.Lookup(actionID)
	if !known {
		writeError(w, http.StatusNotFound, "RECOVERY_ACTION_UNKNOWN",
			"恢复动作目录里没有 "+actionID+"。")
		return
	}
	if !action.Executable() {
		// The refusal text is the catalog's own, which is the sentence the plan
		// showed the operator. A second wording here would be a second answer.
		message := action.Refusal
		if message == "" {
			message = actionID + " 不允许由系统执行。"
		}
		writeError(w, http.StatusConflict, "RECOVERY_ACTION_REFUSED", message)
		return
	}

	// Execution is a physical act with a person waiting on it. The bound is
	// generous because the action may drive a robot, and a timeout that fired
	// mid-motion would report a failure for something that is still happening.
	ctx, cancel := context.WithTimeout(r.Context(), RecoveryExecutionTimeout)
	defer cancel()

	result, err := executor.Execute(ctx, recoveryexec.Request{
		Action: action, PlanID: strings.TrimSpace(body.PlanID), TaskID: strings.TrimSpace(body.TaskID),
		// A person called this endpoint about this action. That is the approval, and
		// it is recorded as such rather than relayed through a port that would
		// answer "yes, because you asked".
		OperatorApproved: true,
	})
	if err != nil {
		writeError(w, http.StatusBadGateway, "RECOVERY_EXECUTION_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, result)
}
