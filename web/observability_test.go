package web

import (
	"io"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strings"
	"testing"
)

func TestFleetWorldPublishesLayeredWebGLConsoleAndBundle(t *testing.T) {
	index, err := assets.ReadFile("index.html")
	if err != nil {
		t.Fatal(err)
	}
	markup := string(index)
	for _, required := range []string{
		`id="fleet-godview-webgl"`, `id="fleet-godview-canvas"`, `id="fleet-world-label-layer"`,
		`id="fleet-visual-state"`, `id="fleet-visual-retry"`, `id="fleet-world-models-toggle"`,
		`id="fleet-world-fixtures-toggle"`, `id="fleet-world-labels-toggle"`, `id="fleet-world-path-toggle"`,
		`id="open-service-console"`,
	} {
		if !strings.Contains(markup, required) {
			t.Errorf("index missing %s", required)
		}
	}
	webgl := strings.Index(markup, `<script src="./webgl_scene.js" defer></script>`)
	world := strings.Index(markup, `<script src="./world_view.js" defer></script>`)
	trace := strings.Index(markup, `<script src="./task_trace.js" defer></script>`)
	app := strings.Index(markup, `<script src="./app.js" defer></script>`)
	if webgl < 0 || world < 0 || trace < 0 || app < 0 || !(webgl < world && world < trace && trace < app) {
		t.Fatal("local deferred scripts must load webgl_scene.js before world_view.js before task_trace.js before app.js")
	}
	if !strings.Contains(markup, `<link rel="stylesheet" href="./styles.css"`) {
		t.Fatal("stylesheet must resolve beside index.html in raw-file and HTTP modes")
	}
	if strings.Contains(markup, `id="fleet-world-label-layer" class="fleet-world-label-layer" aria-live=`) {
		t.Fatal("the complete projected semantic label collection must not be an aria-live region")
	}

	request := httptest.NewRequest("GET", "/webgl_scene.js", nil)
	recorder := httptest.NewRecorder()
	Handler().ServeHTTP(recorder, request)
	response := recorder.Result()
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != 200 || !strings.Contains(string(body), "TangyingWebGL") {
		t.Fatalf("embedded WebGL bundle status=%d body=%q", response.StatusCode, string(body))
	}
}

func TestDocumentResourcesResolveBesideRawFileAndAtHTTPRoot(t *testing.T) {
	resources := []string{"./styles.css", "./webgl_scene.js", "./world_view.js", "./console_ui.js", "./task_trace.js", "./onboarding.js", "./calibration.js", "./app.js"}
	for _, resource := range resources {
		name := strings.TrimPrefix(resource, "./")
		if _, err := assets.ReadFile(name); err != nil {
			t.Errorf("document resource %s is not embedded: %v", name, err)
		}
		request := httptest.NewRequest("GET", "/"+name, nil)
		recorder := httptest.NewRecorder()
		Handler().ServeHTTP(recorder, request)
		if recorder.Code != 200 {
			t.Errorf("document resource %s returned HTTP %d", name, recorder.Code)
		}
	}
	for _, rawBase := range []string{
		"file:///tmp/tangying-console/index.html",
		"http://127.0.0.1:18080/",
	} {
		base, err := url.Parse(rawBase)
		if err != nil {
			t.Fatal(err)
		}
		for _, resource := range resources {
			reference, err := url.Parse(resource)
			if err != nil {
				t.Fatal(err)
			}
			resolved := base.ResolveReference(reference)
			if resolved.Path != strings.TrimSuffix(base.Path, "index.html")+strings.TrimPrefix(resource, "./") {
				t.Errorf("%s from %s resolved to %s", resource, rawBase, resolved.String())
			}
		}
	}
}

func TestSceneUsesSensorViewsWithObservedRobotPose(t *testing.T) {
	index, err := assets.ReadFile("index.html")
	if err != nil {
		t.Fatal(err)
	}
	app, err := assets.ReadFile("app.js")
	if err != nil {
		t.Fatal(err)
	}
	markup := string(index)
	script := string(app)
	for _, required := range []string{`id="scene-frame"`, `id="scene-live-state"`, `id="held-object"`, `id="active-tool"`, `id="model-revision"`, `id="verification-confidence"`} {
		if !strings.Contains(markup, required) {
			t.Errorf("index missing %s", required)
		}
	}
	for _, required := range []string{"/v1/scene/frame", "findRobotEntity", `entity.category === "robot"`, `entity.entityId === "xlerobot"`, "drawRobotFootprint", "STALE", "UNAVAILABLE"} {
		if !strings.Contains(script, required) {
			t.Errorf("app missing %q", required)
		}
	}
	if strings.Contains(script, `fillText("ROBOT", toX(0), toY(0)`) {
		t.Fatal("robot is still rendered at a hard-coded origin")
	}
}

func TestConsoleDiscoversAdaptersAndUsesBackendNeutralIdentity(t *testing.T) {
	index, err := assets.ReadFile("index.html")
	if err != nil {
		t.Fatal(err)
	}
	app, err := assets.ReadFile("app.js")
	if err != nil {
		t.Fatal(err)
	}
	markup := string(index)
	script := string(app)
	for _, required := range []string{"payload.adapters", "syncAdapters", "adapterInput.replaceChildren", "robotIdentity", "updateSceneIdentity"} {
		if !strings.Contains(script, required) {
			t.Errorf("app missing backend-neutral behavior %q", required)
		}
	}
	if strings.Contains(markup, `<option value="mujoco">`) || strings.Contains(markup, `<option value="xlerobot_direct">`) {
		t.Fatal("adapter selector still hard-codes runtime backends")
	}
	for _, forbidden := range []string{"XLeRobot 工作台操作台", "实时 MuJoCo 场景", "MuJoCo 实时 overview"} {
		if strings.Contains(markup, forbidden) {
			t.Errorf("scene markup makes a backend-specific claim: %q", forbidden)
		}
	}
}

func TestFrameErrorsAreAccessibleAndTelemetryHTTPFailuresDowngradeState(t *testing.T) {
	index, err := assets.ReadFile("index.html")
	if err != nil {
		t.Fatal(err)
	}
	app, err := assets.ReadFile("app.js")
	if err != nil {
		t.Fatal(err)
	}
	markup := string(index)
	script := string(app)
	frameStatus := regexp.MustCompile(`<[^>]*\bid="scene-frame-message"[^>]*>`).FindString(markup)
	if !strings.Contains(frameStatus, `role="status"`) || !strings.Contains(frameStatus, `aria-live="polite"`) {
		t.Fatal("frame status is not exposed as a live status")
	}
	for _, required := range []string{"handleTelemetryFailure", `if (!response.ok) {`, `clearSceneFrame(`, `setSceneVisualState("UNAVAILABLE"`} {
		if !strings.Contains(script, required) {
			t.Errorf("app missing explicit telemetry failure transition %q", required)
		}
	}
}
