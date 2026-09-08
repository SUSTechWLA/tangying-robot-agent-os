package console

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func WithEvidence(store tasks.EvidenceStore) Option {
	return func(server *Server) { server.evidence = store }
}

func (s *Server) evidenceRoutes() {
	s.mux.HandleFunc("GET /v1/tasks/{id}/observations", s.listEvidence)
	s.mux.HandleFunc("GET /v1/tasks/{id}/observations/{evidence}", s.getEvidence)
	s.mux.HandleFunc("GET /v1/tasks/{id}/observations/{evidence}/rgb", s.getEvidenceRGB)
	s.mux.HandleFunc("GET /v1/tasks/{id}/observations/{evidence}/depth", s.getEvidenceDepth)
}

func (s *Server) evidenceTask(w http.ResponseWriter, r *http.Request) bool {
	if s.evidence == nil {
		writeError(w, http.StatusServiceUnavailable, "EVIDENCE_UNAVAILABLE", "historical observation storage is not configured")
		return false
	}
	if !tasks.ValidEvidenceKey(r.PathValue("id")) {
		writeError(w, http.StatusNotFound, "TASK_NOT_FOUND", "task not found")
		return false
	}
	_, err := s.service.Get(r.Context(), r.PathValue("id"))
	if errors.Is(err, tasks.ErrTaskNotFound) {
		writeError(w, http.StatusNotFound, "TASK_NOT_FOUND", "task not found")
		return false
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "EVIDENCE_READ_FAILED", "task history could not be read")
		return false
	}
	return true
}

func (s *Server) listEvidence(w http.ResponseWriter, r *http.Request) {
	if !s.evidenceTask(w, r) {
		return
	}
	limit := 100
	var err error
	if text := r.URL.Query().Get("limit"); text != "" {
		limit, err = strconv.Atoi(text)
	}
	if err != nil || limit < 1 || limit > 200 {
		writeError(w, http.StatusBadRequest, "INVALID_EVIDENCE_CURSOR", "limit must be between 1 and 200")
		return
	}
	var before int64
	if text := r.URL.Query().Get("before"); text != "" {
		before, err = strconv.ParseInt(text, 10, 64)
	}
	if err != nil || before < 0 {
		writeError(w, http.StatusBadRequest, "INVALID_EVIDENCE_CURSOR", "before must be a nonnegative record index")
		return
	}
	records, err := s.evidence.ListEvidence(r.Context(), r.PathValue("id"), limit, before)
	if err != nil {
		evidenceError(w, err)
		return
	}
	if records == nil {
		records = []tasks.EvidenceRecord{}
	}
	response := map[string]any{"taskId": r.PathValue("id"), "historical": true, "records": records}
	if len(records) == limit {
		response["nextBefore"] = records[len(records)-1].RecordIndex
	}
	w.Header().Set("Cache-Control", "private, no-store")
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) readEvidence(w http.ResponseWriter, r *http.Request) (tasks.EvidenceRecord, bool) {
	if !s.evidenceTask(w, r) {
		return tasks.EvidenceRecord{}, false
	}
	record, err := s.evidence.Evidence(r.Context(), r.PathValue("id"), r.PathValue("evidence"))
	if err != nil {
		evidenceError(w, err)
		return tasks.EvidenceRecord{}, false
	}
	if record.TaskID != r.PathValue("id") || record.ID != r.PathValue("evidence") {
		evidenceError(w, tasks.ErrEvidenceNotFound)
		return tasks.EvidenceRecord{}, false
	}
	w.Header().Set("Cache-Control", "private, no-store")
	return record, true
}

func (s *Server) getEvidence(w http.ResponseWriter, r *http.Request) {
	record, ok := s.readEvidence(w, r)
	if !ok {
		return
	}
	writeJSON(w, http.StatusOK, record)
}

func (s *Server) getEvidenceRGB(w http.ResponseWriter, r *http.Request) { s.evidenceImage(w, r, false) }
func (s *Server) getEvidenceDepth(w http.ResponseWriter, r *http.Request) {
	s.evidenceImage(w, r, true)
}

func (s *Server) evidenceImage(w http.ResponseWriter, r *http.Request, depth bool) {
	record, ok := s.readEvidence(w, r)
	if !ok {
		return
	}
	if record.Expired {
		writeError(w, http.StatusGone, "EVIDENCE_EXPIRED", "historical raw content expired; capture metadata and checksums remain available")
		return
	}
	data, mediaType, hash := record.RGB, record.RGBMediaType, record.RGBSHA256
	if depth {
		data, mediaType, hash = record.Depth, record.DepthMediaType, record.DepthSHA256
	}
	if len(data) == 0 {
		writeError(w, http.StatusNotFound, "EVIDENCE_IMAGE_UNAVAILABLE", "this capture did not include the requested image")
		return
	}
	w.Header().Set("Content-Type", mediaType)
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.Header().Set("ETag", `"`+hash+`"`)
	w.Header().Set("X-Evidence-Id", record.ID)
	w.Header().Set("X-Observed-At-Unix-Ms", strconv.FormatInt(record.ObservedAtUnixMS, 10))
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

func evidenceError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, tasks.ErrEvidenceNotFound), errors.Is(err, tasks.ErrTaskNotFound):
		writeError(w, http.StatusNotFound, "EVIDENCE_NOT_FOUND", "capture not found for this task")
	case errors.Is(err, tasks.ErrEvidenceCorrupt):
		writeError(w, http.StatusInternalServerError, "EVIDENCE_CHECKSUM_FAILED", "stored capture checksum validation failed")
	default:
		writeError(w, http.StatusInternalServerError, "EVIDENCE_READ_FAILED", "historical capture could not be read")
	}
}
