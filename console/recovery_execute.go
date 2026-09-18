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
// This endpoint executes a physical action, so what authorises it has to be
// nameable. It is authorised by a console session: a request that carries this
// process's session token, which a page gets by loading this console and a
// cross-site request cannot get at all. The token is minted per process and
// compared in constant time (guard.go).
//
// That is a narrower claim than "a person approved this", and it is the claim
// this code is entitled to make. The console has no user accounts; inventing an
// identity here would be a weaker second system beside the Fleet's real auth. So
// what is recorded is the session, not a person: see OperatorApproved below.
//
// What this endpoint does *not* do is execute anything without a request: there
// is no timer, no automatic retry and no path from a plan to a call that does
// not pass through a session-bearing call.
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
	if !s.allowOperatorWrite(w, r) {
		return
	}
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
		// The gate above is what makes this true, and it is true in a narrower
		// sense than the field name suggests: a client holding this process's
		// console session called about this action. It is not "a named person
		// approved this", because the console has no user accounts to name one
		// with — but it is no longer "somebody called the endpoint" either, which
		// is all the hardcoded `true` used to mean.
		OperatorApproved: true,
		// The evidence, recorded so a later reader can tell which console
		// session asked rather than having to take the boolean on faith.
		ApprovalEvidence: s.operatorEvidence(r),
	})
	if err != nil {
		writeError(w, http.StatusBadGateway, "RECOVERY_EXECUTION_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, result)
}
