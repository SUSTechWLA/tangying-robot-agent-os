// Package modelroute resolves independent model endpoints for each decision
// stage. An edge deployment can use a small local model for one stage and the
// fleet's authenticated reasoning service for another.
package modelroute

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const (
	Intent               = "INTENT"
	Planning             = "PLANNING"
	Recovery             = "RECOVERY"
	System               = "SYSTEM"
	Goal                 = "GOAL"
	MinStageOutputTokens = 128
	MaxStageOutputTokens = 16384
)

type Endpoint struct {
	Provider string
	BaseURL  string
	APIKey   string
	Model    string
	Cache    CachePolicy
	// OutputTokens overrides the global action-loop output reserve; zero inherits it.
	OutputTokens int
	UsageFile    string
	outputRaw    string
	cacheStage   string
}

// Resolve applies per-stage overrides to the legacy AGENT_* defaults. An
// explicitly empty stage value clears an inherited value. A stage that changes
// BaseURL must name its own model; its key defaults to empty unless explicitly
// set. Inheriting a key across origins would send it to the wrong server.
func Resolve(values map[string]string, stage string) Endpoint {
	read := func(field string) string {
		if value, ok := values["AGENT_"+stage+"_"+field]; ok {
			return strings.TrimSpace(value)
		}
		return strings.TrimSpace(values["AGENT_"+field])
	}
	provider := read("PROVIDER")
	if provider == "" {
		provider = "deterministic"
	}
	baseURL := read("BASE_URL")
	model := read("MODEL")
	apiKey := read("API_KEY")
	stageBaseURL := strings.TrimRight(strings.TrimSpace(values["AGENT_"+stage+"_BASE_URL"]), "/")
	legacyBaseURL := strings.TrimRight(strings.TrimSpace(values["AGENT_BASE_URL"]), "/")
	if stageBaseURL != "" && stageBaseURL != legacyBaseURL {
		if _, explicit := values["AGENT_"+stage+"_MODEL"]; !explicit {
			model = ""
		}
		if _, explicit := values["AGENT_"+stage+"_API_KEY"]; !explicit {
			apiKey = ""
		}
	}
	outputRaw := strings.TrimSpace(values["AGENT_"+stage+"_OUTPUT_TOKENS"])
	outputTokens, _ := strconv.Atoi(outputRaw)
	return Endpoint{Provider: provider, BaseURL: baseURL, APIKey: apiKey, Model: model,
		OutputTokens: outputTokens, outputRaw: outputRaw, UsageFile: strings.TrimSpace(values["AGENT_MODEL_USAGE_FILE"]),
		Cache: CachePolicy{Mode: strings.ToLower(strings.TrimSpace(values["AGENT_"+stage+"_CACHE_MODE"])), Namespace: strings.TrimSpace(values["AGENT_"+stage+"_CACHE_NAMESPACE"])}, cacheStage: stage}
}

func Environment(stage string) Endpoint {
	values := map[string]string{}
	for _, field := range []string{"PROVIDER", "BASE_URL", "API_KEY", "MODEL"} {
		legacy := "AGENT_" + field
		stageKey := "AGENT_" + stage + "_" + field
		values[legacy] = os.Getenv(legacy)
		if value := os.Getenv(stageKey); value != "" {
			values[stageKey] = value
		}
	}
	// Cache hints deliberately have no global fallback across stages/origins.
	for _, field := range []string{"CACHE_MODE", "CACHE_NAMESPACE", "OUTPUT_TOKENS"} {
		key := "AGENT_" + stage + "_" + field
		values[key] = os.Getenv(key)
	}
	values["AGENT_MODEL_USAGE_FILE"] = os.Getenv("AGENT_MODEL_USAGE_FILE")
	return Resolve(values, stage)
}

func (e Endpoint) Validate() error {
	if e.outputRaw != "" {
		if value, err := strconv.Atoi(e.outputRaw); err != nil || value <= 0 {
			return errors.New("stage OUTPUT_TOKENS must be a positive integer")
		}
	}
	if e.OutputTokens != 0 && (e.OutputTokens < MinStageOutputTokens || e.OutputTokens > MaxStageOutputTokens) {
		return fmt.Errorf("stage OUTPUT_TOKENS must be between %d and %d", MinStageOutputTokens, MaxStageOutputTokens)
	}
	if e.OutputTokens != 0 && (!strings.EqualFold(e.Provider, "openai") || e.cacheStage != "" && e.cacheStage != Goal && e.cacheStage != Recovery) {
		return errors.New("stage OUTPUT_TOKENS is supported only by GOAL and RECOVERY openai-compatible action loops")
	}
	if e.UsageFile != "" && !filepath.IsAbs(e.UsageFile) {
		return errors.New("AGENT_MODEL_USAGE_FILE must be an absolute path")
	}
	if err := e.Cache.Validate(); err != nil {
		return err
	}
	if e.Cache.EffectiveMode() != "off" {
		if !strings.EqualFold(e.Provider, "openai") {
			return errors.New("cache hints require an openai-compatible provider")
		}
		if e.cacheStage != "" && e.cacheStage != Goal && e.cacheStage != Recovery {
			return errors.New("cache hints are supported only by GOAL and RECOVERY action-loop stages")
		}
	}
	switch strings.ToLower(e.Provider) {
	case "deterministic":
		return nil
	case "openai":
		if e.Model == "" || e.BaseURL == "" {
			return errors.New("openai model requires base URL and model name")
		}
		parsed, err := url.Parse(e.BaseURL)
		if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
			return fmt.Errorf("invalid model base URL %q", e.BaseURL)
		}
		return nil
	default:
		return fmt.Errorf("unsupported model provider %q", e.Provider)
	}
}

// Assist authenticates requests to the cloud's read-only reasoning endpoint.
// Only a stage whose BaseURL equals URL receives these credentials.
type Assist struct {
	URL         string
	RobotID     string
	DeviceToken string
	CAFile      string
}

func (a Assist) ClientFor(endpoint Endpoint) (*http.Client, error) {
	if a.URL == "" || strings.TrimRight(a.URL, "/") != strings.TrimRight(endpoint.BaseURL, "/") {
		return nil, nil
	}
	if endpoint.Cache.EffectiveMode() != "off" {
		return nil, errors.New("cloud model assist does not accept cache hints; use cache mode off")
	}
	if a.RobotID == "" || a.DeviceToken == "" {
		return nil, errors.New("cloud model assist requires robot ID and device token")
	}
	parsed, err := url.Parse(a.URL)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.EscapedPath() != "/v1/assist" {
		return nil, errors.New("cloud model assist URL must be an HTTPS /v1/assist endpoint")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if a.CAFile != "" {
		pem, err := os.ReadFile(a.CAFile)
		if err != nil {
			return nil, err
		}
		roots := x509.NewCertPool()
		if !roots.AppendCertsFromPEM(pem) {
			return nil, errors.New("cloud model assist CA contains no certificates")
		}
		transport.TLSClientConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	}
	return &http.Client{Timeout: 65 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		Transport:     &assistTransport{base: transport, target: strings.TrimRight(a.URL, "/") + "/chat/completions", robotID: a.RobotID, token: a.DeviceToken}}, nil
}

type assistTransport struct {
	base    http.RoundTripper
	target  string
	robotID string
	token   string
}

func (t *assistTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if request.URL.String() != t.target {
		return nil, errors.New("cloud model assist request is outside the configured endpoint")
	}
	copy := request.Clone(request.Context())
	copy.Header.Set("X-Robot-ID", t.robotID)
	copy.Header.Set("X-Device-Token", t.token)
	return t.base.RoundTrip(copy)
}
