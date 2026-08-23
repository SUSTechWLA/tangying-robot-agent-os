package policy

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestHTTPProviderDiscoversManifestAndValidatesInference(t *testing.T) {
	manifest := validManifest()
	revision, err := manifest.Revision()
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/v1/manifest":
			_ = json.NewEncoder(writer).Encode(manifest)
		case "/v1/infer":
			var input InferenceRequest
			if err := json.NewDecoder(request.Body).Decode(&input); err != nil {
				t.Error(err)
			}
			_ = json.NewEncoder(writer).Encode(InferenceResult{
				SchemaVersion: "policy.inference.result.v1", RequestID: input.RequestID,
				CommandID: input.CommandID, ManifestRevision: revision, InferenceID: "infer-http-1",
				ObservationID: input.Observation.ObservationID,
				Actions:       []Action{{Values: map[string]float64{"left_arm_shoulder.pos": 3}}},
			})
		default:
			http.NotFound(writer, request)
		}
	}))
	defer server.Close()

	provider, err := NewHTTPProvider(HTTPConfig{Endpoint: server.URL, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	discovered, err := provider.Manifest(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if discovered.PolicyID != manifest.PolicyID {
		t.Fatalf("manifest = %#v", discovered)
	}
	request := validRequest(t, time.Now().UTC())
	decision, err := provider.Infer(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if decision.InferenceID != "infer-http-1" || decision.Actions[0]["left_arm_shoulder.pos"] != 3 {
		t.Fatalf("decision = %#v", decision)
	}
}

func TestHTTPProviderRejectsManifestDriftBetweenDiscoveryAndInference(t *testing.T) {
	manifest := validManifest()
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/v1/manifest" {
			requests++
			current := manifest
			if requests > 1 {
				current.Version = "changed"
			}
			_ = json.NewEncoder(writer).Encode(current)
			return
		}
		_ = json.NewEncoder(writer).Encode(InferenceResult{})
	}))
	defer server.Close()
	provider, err := NewHTTPProvider(HTTPConfig{Endpoint: server.URL, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := provider.Manifest(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := provider.Infer(context.Background(), validRequest(t, time.Now().UTC())); !errors.Is(err, ErrManifestDrift) {
		t.Fatalf("error = %v", err)
	}
}

func TestHTTPProviderBoundsResponsesAndRedactsRemoteBody(t *testing.T) {
	secret := "device-token-must-not-leak"
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusInternalServerError)
		_, _ = writer.Write([]byte(strings.Repeat(secret, 128)))
	}))
	defer server.Close()
	provider, err := NewHTTPProvider(HTTPConfig{Endpoint: server.URL, Timeout: time.Second, MaxResponseBytes: 64})
	if err != nil {
		t.Fatal(err)
	}
	_, err = provider.Manifest(context.Background())
	if !errors.Is(err, ErrProviderUnavailable) || strings.Contains(err.Error(), secret) {
		t.Fatalf("error = %v", err)
	}
}

func TestHTTPProviderMapsDeadlineWithoutLeakingTransportDetails(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		<-request.Context().Done()
	}))
	defer server.Close()
	provider, err := NewHTTPProvider(HTTPConfig{Endpoint: server.URL, Timeout: 5 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	_, err = provider.Manifest(context.Background())
	if !errors.Is(err, ErrProviderTimeout) {
		t.Fatalf("error = %v", err)
	}
}
