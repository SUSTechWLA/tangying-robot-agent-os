package fleet

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
)

// ModelAssist is a read-only, model-backed tool. It never sees robot runtime
// credentials and never dispatches actions. Edge callers must still validate
// plans and pass every physical action through the normal approval gates.
type ModelAssist struct {
	baseURL         string
	apiKey          string
	model           string
	stageModels     map[string]string
	maxOutputTokens int
	client          *http.Client
	slots           chan struct{}
	mu              sync.Mutex
	active          map[string]int
}

type ModelAssistConfig struct {
	BaseURL         string
	APIKey          string
	Model           string
	StageModels     map[string]string
	MaxOutputTokens int
	MaxConcurrent   int
	Client          *http.Client
}

func NewModelAssist(config ModelAssistConfig) (*ModelAssist, error) {
	parsed, err := url.Parse(config.BaseURL)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("model assist requires an HTTP(S) base URL")
	}
	models := make(map[string]string, len(config.StageModels))
	for stage, model := range config.StageModels {
		if stage != "cloud-intent" && stage != "cloud-planning" && stage != "cloud-recovery" {
			return nil, errors.New("unsupported model assist stage alias")
		}
		if strings.TrimSpace(model) == "" {
			return nil, errors.New("model assist stage model cannot be empty")
		}
		models[stage] = model
	}
	if config.Model == "" && len(models) == 0 {
		return nil, errors.New("model assist requires a model name")
	}
	if config.MaxOutputTokens < 0 || config.MaxOutputTokens > 32768 {
		return nil, errors.New("model assist output token cap must be 1..32768")
	}
	if config.MaxOutputTokens == 0 {
		config.MaxOutputTokens = 4096
	}
	if config.MaxConcurrent < 0 {
		return nil, errors.New("model assist concurrency must be positive")
	}
	if config.MaxConcurrent == 0 {
		config.MaxConcurrent = 16
	}
	if config.MaxConcurrent > 1024 {
		return nil, errors.New("model assist concurrency must be at most 1024")
	}
	if config.Client == nil {
		config.Client = &http.Client{Timeout: 60 * time.Second}
	}
	client := *config.Client
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &ModelAssist{baseURL: strings.TrimRight(config.BaseURL, "/"), apiKey: config.APIKey,
		model: config.Model, stageModels: models, maxOutputTokens: config.MaxOutputTokens,
		client: &client, slots: make(chan struct{}, config.MaxConcurrent), active: map[string]int{}}, nil
}

func (s *Server) modelCompletion(w http.ResponseWriter, r *http.Request) {
	robotID, ok := auth.AssistRobotID(r.Context())
	if !ok {
		writeError(w, http.StatusForbidden, "DEVICE_PRINCIPAL_REQUIRED", "robot device credential required")
		return
	}
	if s.modelAssist == nil {
		writeError(w, http.StatusServiceUnavailable, "ASSIST_UNAVAILABLE", "cloud model assist is not configured")
		return
	}
	s.modelAssist.serveHTTP(w, r, robotID)
}

func (a *ModelAssist) serveHTTP(w http.ResponseWriter, r *http.Request, robotID string) {
	const maxRequest = 64 << 10
	const maxResponse = 1 << 20
	wire, err := io.ReadAll(io.LimitReader(r.Body, maxRequest+1))
	if err != nil || len(wire) > maxRequest {
		writeError(w, http.StatusRequestEntityTooLarge, "ASSIST_REQUEST_TOO_LARGE", "model request exceeds limit")
		return
	}
	var request map[string]json.RawMessage
	if err := json.Unmarshal(wire, &request); err != nil || request == nil {
		writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "JSON object required")
		return
	}
	if _, ok := request["messages"]; !ok {
		writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "messages required")
		return
	}
	if raw := request["stream"]; len(raw) != 0 && string(raw) != "false" {
		writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "streaming is unsupported")
		return
	}
	for field := range request {
		switch field {
		case "model", "messages", "tools", "tool_choice", "parallel_tool_calls", "temperature", "top_p", "max_tokens", "max_completion_tokens", "stream":
		default:
			writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "unsupported model request field")
			return
		}
	}
	var messages []struct {
		Role    string `json:"role"`
		Content string `json:"content"`
	}
	if err := json.Unmarshal(request["messages"], &messages); err != nil || len(messages) == 0 || len(messages) > 64 {
		writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "1..64 text messages required")
		return
	}
	for _, message := range messages {
		if message.Role != "system" && message.Role != "user" && message.Role != "assistant" || message.Content == "" {
			writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "unsupported message role or empty content")
			return
		}
	}
	alias := "cloud-assist"
	if raw := request["model"]; len(raw) != 0 {
		if err := json.Unmarshal(raw, &alias); err != nil {
			writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "model alias must be text")
			return
		}
	}
	model := a.model
	if alias != "cloud-assist" {
		model = a.stageModels[alias]
	}
	if model == "" {
		writeError(w, http.StatusBadRequest, "INVALID_ASSIST_REQUEST", "model alias is not configured")
		return
	}
	request["model"], _ = json.Marshal(model)
	request["max_tokens"], _ = json.Marshal(a.maxOutputTokens)
	delete(request, "max_completion_tokens")
	wire, _ = json.Marshal(request)
	if !a.acquire(robotID) {
		writeError(w, http.StatusTooManyRequests, "ASSIST_BUSY", "cloud model capacity is busy")
		return
	}
	defer a.release(robotID)
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	upstream, err := http.NewRequestWithContext(ctx, http.MethodPost, a.baseURL+"/chat/completions", bytes.NewReader(wire))
	if err != nil {
		writeError(w, http.StatusBadGateway, "ASSIST_FAILED", "could not create model request")
		return
	}
	upstream.Header.Set("Content-Type", "application/json")
	if a.apiKey != "" {
		upstream.Header.Set("Authorization", "Bearer "+a.apiKey)
	}
	response, err := a.client.Do(upstream)
	if err != nil {
		writeError(w, http.StatusBadGateway, "ASSIST_FAILED", "cloud model request failed")
		return
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		log.Printf("model assist robot=%s alias=%s upstream_status=%d", robotID, alias, response.StatusCode)
		writeError(w, http.StatusBadGateway, "ASSIST_FAILED", "cloud model rejected request")
		return
	}
	result, err := io.ReadAll(io.LimitReader(response.Body, maxResponse+1))
	if err != nil || len(result) > maxResponse || !json.Valid(result) {
		writeError(w, http.StatusBadGateway, "ASSIST_FAILED", "cloud model returned an invalid response")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	log.Printf("model assist robot=%s alias=%s status=ok", robotID, alias)
	_, _ = w.Write(result)
}

// A single robot may occupy at most two model slots, so one stuck or noisy
// device cannot consume the full cloud reasoning pool for the rest of a fleet.
func (a *ModelAssist) acquire(robotID string) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.active[robotID] >= 2 {
		return false
	}
	select {
	case a.slots <- struct{}{}:
		a.active[robotID]++
		return true
	default:
		return false
	}
}

func (a *ModelAssist) release(robotID string) {
	a.mu.Lock()
	a.active[robotID]--
	if a.active[robotID] == 0 {
		delete(a.active, robotID)
	}
	<-a.slots
	a.mu.Unlock()
}
