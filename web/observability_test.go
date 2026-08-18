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
	for _, required := range []string{"/v1/scene/frame", `entity.entityId === "xlerobot"`, "drawRobotFootprint", "STALE", "UNAVAILABLE"} {
		if !strings.Contains(script, required) {
			t.Errorf("app missing %q", required)
		}
	}
	if strings.Contains(script, `fillText("ROBOT", toX(0), toY(0)`) {
		t.Fatal("robot is still rendered at a hard-coded origin")
	}
}
