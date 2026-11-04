package robotclient

import (
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

func TestFailedCommandDiagnosticsAreBoundedAndNeverAuthorizeSuccess(t *testing.T) {
	for _, tc := range []struct {
		name, raw string
		success   bool
		want      string
	}{
		{"failed", ` { "observation": {"nav2Action":{"errorCode":208}}, "commandId":"diagnostic" } `, false, `{"commandId":"diagnostic","observation":{"nav2Action":{"errorCode":208}}}`},
		{"success", `{"commandId":"diagnostic","ready":true}`, true, ""},
		{"malformed", `{"commandId":`, false, ""},
		{"array", `[{"commandId":"diagnostic"}]`, false, ""},
		{"wrong_command", `{"commandId":"different"}`, false, ""},
		{"oversized", `{"commandId":"diagnostic","data":"` + strings.Repeat("x", maxFailureDiagnosticsBytes) + `"}`, false, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c, s := strictScene(t)
			kind := robotv1.SkillEventType_SKILL_EVENT_FAILED
			if tc.success {
				kind = robotv1.SkillEventType_SKILL_EVENT_SUCCEEDED
			}
			details, _ := structpb.NewStruct(map[string]any{"navigationFailureJson": tc.raw})
			s.terminal = &robotv1.SkillEvent{Type: kind, Code: "NAV2_ACTION_ENDED", Details: details}
			result, err := c.Invoke(t.Context(), runtime.Command{CommandID: "diagnostic", Capability: "observe_scene", Deadline: time.Now().Add(time.Second)})
			if err != nil {
				t.Fatal(err)
			}
			if result.Success != tc.success || result.Code != "NAV2_ACTION_ENDED" || result.FailureDiagnosticsJSON != tc.want {
				t.Fatalf("result changed or diagnostics misbound: %+v", result)
			}
		})
	}
}
