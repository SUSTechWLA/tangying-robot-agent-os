package web

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestVersionedGLBIsEmbeddedAndImmutable(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/assets/scenes/robocasa-handoff-v1/xlerobot.glb?v="+strings.Repeat("a", 64), nil)
	recorder := httptest.NewRecorder()

	Handler().ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d, want %d", recorder.Code, http.StatusOK)
	}
	if contentType := recorder.Header().Get("Content-Type"); contentType != "model/gltf-binary" {
		t.Fatalf("content-type=%q, want model/gltf-binary", contentType)
	}
	if cacheControl := recorder.Header().Get("Cache-Control"); !strings.Contains(cacheControl, "immutable") {
		t.Fatalf("cache=%q, want immutable", cacheControl)
	}
}

func TestManifestIsEmbeddedAndNotCachedImmutably(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/assets/scenes/robocasa-handoff-v1/manifest.json", nil)
	recorder := httptest.NewRecorder()

	Handler().ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d, want %d", recorder.Code, http.StatusOK)
	}
	if cacheControl := recorder.Header().Get("Cache-Control"); cacheControl != "no-cache" {
		t.Fatalf("cache=%q, want no-cache", cacheControl)
	}
}

func TestHandlerRejectsUncleanOrEscapedAssetPaths(t *testing.T) {
	for _, target := range []string{
		"/assets/../v1/tasks",
		"/assets/%2e%2e/v1/tasks",
		"/assets/%2f..%2fv1/tasks",
	} {
		t.Run(target, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, target, nil)
			recorder := httptest.NewRecorder()

			Handler().ServeHTTP(recorder, request)

			if recorder.Code != http.StatusNotFound {
				t.Fatalf("status=%d, want %d", recorder.Code, http.StatusNotFound)
			}
		})
	}
}

func TestFleetPageEmbedsPlainLanguageVersionedMissionExperience(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	recorder := httptest.NewRecorder()

	Handler().ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d, want %d", recorder.Code, http.StatusOK)
	}
	body := recorder.Body.String()
	for _, required := range []string{
		`id="fleet-mission-rail"`,
		`id="fleet-mission-understanding"`,
		`id="fleet-step-ribbon"`,
		`id="fleet-tool-activities"`,
		`id="fleet-update-request"`,
		`id="fleet-revision-confirm"`,
		`id="fleet-task-experience-status"`,
	} {
		if !strings.Contains(body, required) {
			t.Fatalf("Fleet page missing mission experience contract %s", required)
		}
	}
}
