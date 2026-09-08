package console_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
)

func TestNavigationMapProxyKeepsUnknownCellsAndDoesNotExposeCredentials(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer private-token" || r.URL.Query().Get("includeGrid") != "1" {
			t.Error("missing private upstream auth or grid request")
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"robotId": "robot-1", "frameId": "map", "ready": true,
			"mapRevision": "map-one", "width": 2, "height": 2, "resolution": .025, "cells": []int{-1, 0, 100, 50},
			"origin": []float64{0, 0, 0, 1, 0, 0, 0}, "mapPose": []float64{0, .05, 0, 1, 0, 0, 0},
			"poseObservedAtUnixMs": time.Now().UnixMilli(), "poseSource": "rtabmap_tf", "token": "must-never-leak"})
	}))
	defer upstream.Close()
	reader, err := console.NewNavigationReader(upstream.URL, "private-token")
	if err != nil {
		t.Fatal(err)
	}
	server := console.NewServer(nil, nil, console.WithNavigation(reader))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest("GET", "/v1/navigation/map", nil))
	if response.Code != 200 {
		t.Fatalf("%d %s", response.Code, response.Body.String())
	}
	if strings.Contains(response.Body.String(), "token") || !strings.Contains(response.Body.String(), `"cells":[-1,0,100,50]`) {
		t.Fatal(response.Body.String())
	}
	if response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("map cached")
	}
}

func TestNavigationMapRejectsBrokenGridAndRedirects(t *testing.T) {
	for _, body := range []string{`{"width":2,"height":2,"resolution":0.1,"cells":[0]}`, `{"width":2,"height":2,"resolution":0.1,"cells":[-1,0,101,0]}`} {
		upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }))
		reader, _ := console.NewNavigationReader(upstream.URL, "token")
		recorder := httptest.NewRecorder()
		console.NewServer(nil, nil, console.WithNavigation(reader)).Handler().ServeHTTP(recorder, httptest.NewRequest("GET", "/v1/navigation/map", nil))
		upstream.Close()
		if recorder.Code != 502 {
			t.Fatalf("broken grid accepted: %d", recorder.Code)
		}
	}
	var leaked bool
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaked = true }))
	defer destination.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, destination.URL, 302) }))
	defer upstream.Close()
	reader, _ := console.NewNavigationReader(upstream.URL, "token")
	recorder := httptest.NewRecorder()
	console.NewServer(nil, nil, console.WithNavigation(reader)).Handler().ServeHTTP(recorder, httptest.NewRequest("GET", "/v1/navigation/map", nil))
	if recorder.Code != 502 || leaked {
		t.Fatal("redirect was followed")
	}
}

func TestNavigationMapDoesNotConvertNullGeometryToFreeSpaceOrOrigin(t *testing.T) {
	for _, field := range []string{"cells", "origin", "mapPose"} {
		t.Run(field, func(t *testing.T) {
			body := map[string]any{"robotId": "robot-1", "frameId": "map", "ready": true,
				"mapRevision": "map-one", "width": 2, "height": 2, "resolution": .025,
				"cells": []any{-1, 0, 100, 50}, "origin": []any{0, 0, 0, 1, 0, 0, 0},
				"mapPose": []any{0, .05, 0, 1, 0, 0, 0}, "poseSource": "rtabmap_tf",
				"poseObservedAtUnixMs": time.Now().UnixMilli()}
			body[field].([]any)[0] = nil
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _ = json.NewEncoder(w).Encode(body) }))
			defer upstream.Close()
			reader, _ := console.NewNavigationReader(upstream.URL, "token")
			recorder := httptest.NewRecorder()
			console.NewServer(nil, nil, console.WithNavigation(reader)).Handler().ServeHTTP(recorder, httptest.NewRequest("GET", "/v1/navigation/map", nil))
			if recorder.Code != 502 {
				t.Fatalf("null geometry was accepted as zero: %d %s", recorder.Code, recorder.Body.String())
			}
		})
	}
}

func TestNavigationMapKeepsSavedGridButRemovesStaleRobotPose(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"robotId": "robot-1", "frameId": "map", "ready": true,
			"mapRevision": "saved-map", "width": 2, "height": 2, "resolution": .1, "cells": []int{-1, 0, 100, 50},
			"origin": []float64{0, 0, 0, 1, 0, 0, 0}, "mapPose": []float64{0, .05, 0, 1, 0, 0, 0},
			"poseSource": "rtabmap_tf", "observedAtUnixMs": 1, "poseObservedAtUnixMs": time.Now().UnixMilli() - 1001})
	}))
	defer upstream.Close()
	reader, _ := console.NewNavigationReader(upstream.URL, "token")
	recorder := httptest.NewRecorder()
	console.NewServer(nil, nil, console.WithNavigation(reader)).Handler().ServeHTTP(recorder, httptest.NewRequest("GET", "/v1/navigation/map", nil))
	var got console.NavigationMap
	if err := json.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if recorder.Code != 200 || got.Ready || len(got.MapPose) != 0 || len(got.Cells) != 4 {
		t.Fatalf("invalid stale map behavior: %+v", got)
	}
}
