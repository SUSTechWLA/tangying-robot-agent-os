package web

import (
	"strings"
	"testing"
)

func TestSceneUsesLiveFrameWithSemanticFallbackAndObservedRobotPose(t *testing.T) {
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
	if !strings.Contains(markup, `id="scene-frame-message" role="status" aria-live="polite"`) {
		t.Fatal("frame status is not exposed as a live status")
	}
	for _, required := range []string{"handleTelemetryFailure", `if (!response.ok) {`, `setSceneVisualState("STALE"`, `setSceneVisualState("UNAVAILABLE"`} {
		if !strings.Contains(script, required) {
			t.Errorf("app missing explicit telemetry failure transition %q", required)
		}
	}
}
