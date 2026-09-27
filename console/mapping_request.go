package console

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
)

// Compatibility entry: natural language has one planner and one task authority.
// This route creates a draft; the normal task approval starts its execution.
// It never owns a second mapping workflow or guesses intent from a keyword.
func (s *Server) mappingRequest(w http.ResponseWriter, r *http.Request) {
	if !s.allowOperatorWrite(w, r) {
		return
	}
	if s.service == nil {
		writeError(w, 503, "TASK_SERVICE_UNAVAILABLE", "统一任务服务未配置。")
		return
	}
	var body struct {
		Request     string  `json:"request"`
		Adapter     string  `json:"adapter"`
		Environment string  `json:"environment"`
		MaxTravelM  float64 `json:"maxTravelM"`
		MaxLegs     int     `json:"maxLegs"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 200000))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&body); err != nil {
		writeError(w, 400, "INVALID_REQUEST", err.Error())
		return
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		writeError(w, 400, "INVALID_REQUEST", "请求只能包含一个 JSON 对象。")
		return
	}
	if strings.TrimSpace(body.Request) == "" || len(body.Request) > 8000 {
		writeError(w, 400, "INVALID_REQUEST", "request is required")
		return
	}
	if body.Environment != "" || body.MaxTravelM != 0 || body.MaxLegs != 0 {
		writeError(w, 400, "MAPPING_ENTRY_MIGRATED", "请把地点与预算写入完整任务，例如：探索建图最多行驶12米最多1轮。新入口 /v1/tasks；创建后需要确认执行。")
		return
	}
	task, err := s.service.Create(r.Context(), body.Request, body.Adapter)
	if errors.Is(err, intent.ErrUnsupportedIntent) || errors.Is(err, intent.ErrClarificationRequired) {
		writeError(w, 422, "UNSUPPORTED_INTENT", err.Error())
		return
	}
	if err != nil {
		writeError(w, 500, "CREATE_FAILED", err.Error())
		return
	}
	w.Header().Set("Deprecation", "true")
	w.Header().Set("Link", "</v1/tasks>; rel=\"successor-version\"")
	writeJSON(w, http.StatusCreated, task)
}
