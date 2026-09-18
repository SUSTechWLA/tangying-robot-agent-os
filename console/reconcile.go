package console

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

// The clearing path for an unknown physical outcome.
//
// An unconfirmed physical step blocks readiness and forbids retrying the step,
// and both are correct: nothing may proceed on a world that may already have
// changed. What was missing was the other end. The operator who went and looked
// at the robot — the only thing that can resolve it — had nowhere to record what
// they saw, so the block never lifted, and the honest reading of a permanent
// block is that people stop reading it.
//
// So this endpoint exists to record a person's conclusion, and nothing else can
// call it into being: no timer clears a step, no agent may. It is the same
// judgement the recovery executor already makes when it asks a person to approve
// a physical action, applied to the state that judgement leaves behind.

type reconcileRequest struct {
	StepID string `json:"stepId"`
	// Outcome is what the person concluded: HAPPENED, NEVER_ACTED or ABANDONED.
	Outcome string `json:"outcome"`
	// Note says how they established it. Required: the decision that lets work
	// continue is the one a later reader has to be able to disagree with.
	Note string `json:"note"`
}

func (s *Server) reconcileStep(w http.ResponseWriter, r *http.Request) {
	if !s.allowOperatorWrite(w, r) {
		return
	}
	if s.executor == nil {
		writeError(w, http.StatusServiceUnavailable, "RECONCILIATION_UNAVAILABLE",
			"这个部署没有配置执行记录存储，无法记录对账结论。")
		return
	}
	store, ok := s.executor.(middleware.ReconciliationStore)
	if !ok {
		writeError(w, http.StatusServiceUnavailable, "RECONCILIATION_UNAVAILABLE",
			"这个部署的执行记录存储不支持对账记录。")
		return
	}
	var body reconcileRequest
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_RECONCILIATION", "请求格式不正确")
		return
	}
	stepID := strings.TrimSpace(body.StepID)
	outcome := middleware.StepOutcome(strings.ToUpper(strings.TrimSpace(body.Outcome)))
	note := strings.TrimSpace(body.Note)
	if stepID == "" {
		writeError(w, http.StatusBadRequest, "RECONCILIATION_STEP_REQUIRED",
			"需要指定要核对的那一步；没有 stepId 就无从知道核对的是哪一次动作。")
		return
	}
	if !outcome.Valid() {
		writeError(w, http.StatusBadRequest, "RECONCILIATION_OUTCOME_REQUIRED",
			"outcome 必须是 HAPPENED、NEVER_ACTED 或 ABANDONED 之一。")
		return
	}
	if note == "" {
		writeError(w, http.StatusBadRequest, "RECONCILIATION_NOTE_REQUIRED",
			"需要写一句依据：让人继续往下走的那个判断，是后来的人必须能反驳的那个判断。")
		return
	}
	taskID := strings.TrimSpace(r.PathValue("id"))
	if err := store.ReconcileStep(r.Context(), middleware.StepRecord{TaskID: taskID, StepID: stepID},
		middleware.StepReconciliation{
			Outcome: outcome, Actor: s.operatorEvidence(r), Note: note, RecordedAt: time.Now().UTC(),
		}); err != nil {
		writeError(w, http.StatusConflict, "RECONCILIATION_REFUSED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"taskId": taskID, "stepId": stepID, "outcome": string(outcome),
		"recordedAt": time.Now().UTC(),
	})
}
