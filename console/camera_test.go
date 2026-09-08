package console_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

type cameraProvider struct {
	snapshot telemetry.Snapshot
	sources  []string
}

func (p *cameraProvider) TelemetrySource(_ context.Context, _ string, source string) (telemetry.Snapshot, error) {
	p.sources = append(p.sources, source)
	return p.snapshot, nil
}

func TestCameraEndpointReturnsRGBDepthAndCloudFromOneNamedCapture(t *testing.T) {
	snapshot := historicalSnapshot("")
	snapshot.ObservedAt = time.Now()
	snapshot.Reconstruction.ObservedAtUnixMS = snapshot.ObservedAt.UnixMilli()
	provider := &cameraProvider{snapshot: snapshot}
	server := httptest.NewServer(console.NewServer(nil, nil, console.WithCamera(provider)).Handler())
	defer server.Close()
	response, err := http.Get(server.URL + "/v1/scene/camera?adapter=rgbd&sourceId=head")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatalf("status=%d", response.StatusCode)
	}
	var body struct {
		Snapshot telemetry.Snapshot `json:"snapshot"`
		RGB      string             `json:"rgbDataUrl"`
		Depth    string             `json:"depthDataUrl"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if len(provider.sources) != 1 || provider.sources[0] != "head" || body.Snapshot.Reconstruction.ObservationID != snapshot.Reconstruction.ObservationID {
		t.Fatal("source/capture was replaced")
	}
	if body.RGB != "data:image/png;base64,"+base64.StdEncoding.EncodeToString(snapshot.Frame) || body.Depth != "data:image/png;base64,"+base64.StdEncoding.EncodeToString(snapshot.DepthFrame) {
		t.Fatal("images changed")
	}
	if !strings.Contains(response.Header.Get("Cache-Control"), "no-store") {
		t.Fatal("live camera cached")
	}
}

func TestCameraEndpointRejectsWrongSourceStaleAndDamagedCaptures(t *testing.T) {
	for _, which := range []string{"wrong source", "stale", "damaged", "missing depth"} {
		t.Run(which, func(t *testing.T) {
			snapshot := historicalSnapshot("")
			snapshot.ObservedAt = time.Now()
			snapshot.Reconstruction.ObservedAtUnixMS = snapshot.ObservedAt.UnixMilli()
			switch which {
			case "wrong source":
				snapshot.Reconstruction.SourceID = "another"
			case "stale":
				snapshot.ObservedAt = snapshot.ObservedAt.Add(-time.Hour)
			case "damaged":
				snapshot.Frame = []byte("broken")
			case "missing depth":
				snapshot.DepthFrame = nil
			}
			server := httptest.NewServer(console.NewServer(nil, nil, console.WithCamera(&cameraProvider{snapshot: snapshot})).Handler())
			defer server.Close()
			response, err := http.Get(server.URL + "/v1/scene/camera?adapter=rgbd&sourceId=head")
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			if response.StatusCode != 503 {
				t.Fatalf("invalid capture status=%d", response.StatusCode)
			}
		})
	}
}
