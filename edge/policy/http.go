package policy

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const defaultMaxResponseBytes int64 = 1 << 20

type HTTPConfig struct {
	Endpoint         string
	Timeout          time.Duration
	MaxResponseBytes int64
	Client           *http.Client
}

type HTTPProvider struct {
	endpoint string
	client   *http.Client
	maxBody  int64

	mu               sync.Mutex
	expectedRevision string
}

func NewHTTPProvider(config HTTPConfig) (*HTTPProvider, error) {
	parsed, err := url.Parse(strings.TrimRight(config.Endpoint, "/"))
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.User != nil {
		return nil, fmt.Errorf("%w: invalid endpoint", ErrProviderUnavailable)
	}
	if config.Timeout <= 0 {
		config.Timeout = 10 * time.Second
	}
	if config.MaxResponseBytes <= 0 {
		config.MaxResponseBytes = defaultMaxResponseBytes
	}
	client := config.Client
	if client == nil {
		client = &http.Client{Timeout: config.Timeout}
	}
	return &HTTPProvider{endpoint: parsed.String(), client: client, maxBody: config.MaxResponseBytes}, nil
}

func (provider *HTTPProvider) Manifest(ctx context.Context) (Manifest, error) {
	manifest, revision, err := provider.fetchManifest(ctx)
	if err != nil {
		return Manifest{}, err
	}
	provider.mu.Lock()
	if provider.expectedRevision == "" {
		provider.expectedRevision = revision
	}
	provider.mu.Unlock()
	return manifest, nil
}

func (provider *HTTPProvider) Infer(ctx context.Context, request InferenceRequest) (Decision, error) {
	manifest, revision, err := provider.fetchManifest(ctx)
	if err != nil {
		return Decision{}, err
	}
	provider.mu.Lock()
	expected := provider.expectedRevision
	if expected == "" {
		expected = revision
		provider.expectedRevision = revision
	}
	provider.mu.Unlock()
	if revision != expected || request.ManifestRevision != expected {
		return Decision{}, ErrManifestDrift
	}
	if err := ValidateObservation(manifest, request.Observation, time.Now().UTC()); err != nil {
		return Decision{}, err
	}
	wire, err := json.Marshal(request)
	if err != nil {
		return Decision{}, ErrInferenceInvalid
	}
	var result InferenceResult
	if err := provider.doJSON(ctx, http.MethodPost, "/v1/infer", wire, &result); err != nil {
		return Decision{}, err
	}
	return ValidateInference(manifest, request, result)
}

func (provider *HTTPProvider) fetchManifest(ctx context.Context) (Manifest, string, error) {
	var manifest Manifest
	if err := provider.doJSON(ctx, http.MethodGet, "/v1/manifest", nil, &manifest); err != nil {
		return Manifest{}, "", err
	}
	revision, err := manifest.Revision()
	if err != nil {
		return Manifest{}, "", err
	}
	return manifest, revision, nil
}

func (provider *HTTPProvider) doJSON(ctx context.Context, method, path string, body []byte, target any) error {
	request, err := http.NewRequestWithContext(ctx, method, provider.endpoint+path, bytes.NewReader(body))
	if err != nil {
		return ErrProviderUnavailable
	}
	request.Header.Set("Accept", "application/json")
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := provider.client.Do(request)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return ErrProviderTimeout
		}
		if timeout, ok := err.(interface{ Timeout() bool }); ok && timeout.Timeout() {
			return ErrProviderTimeout
		}
		return ErrProviderUnavailable
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, provider.maxBody))
		return fmt.Errorf("%w: HTTP %d", ErrProviderUnavailable, response.StatusCode)
	}
	limited := io.LimitReader(response.Body, provider.maxBody+1)
	wire, err := io.ReadAll(limited)
	if err != nil {
		return ErrProviderUnavailable
	}
	if int64(len(wire)) > provider.maxBody {
		return ErrResponseTooLarge
	}
	decoder := json.NewDecoder(bytes.NewReader(wire))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return ErrProviderUnavailable
	}
	return nil
}
