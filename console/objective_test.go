package console_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/types/known/structpb"
)

type goalServices struct{ calls int }

func (*goalServices) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	schema, _ := structpb.NewStruct(map[string]any{"type": "object", "additionalProperties": false})
	contract, _ := structpb.NewStruct(map[string]any{"version": "1", "effects": []any{"READ"}})
	return &robotv1.ServiceCatalog{RobotId: "local-test", Services: []*robotv1.ServiceDefinition{{Name: "calibration.get", Available: true, InputSchema: schema, Contract: contract}}}, nil
}
func (p *goalServices) CallService(context.Context, *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.calls++
	return &robotv1.ServiceResponse{Ok: true}, nil
}

func TestLocalObjectiveEndpointUsesSessionAndCreatesOnlyUnapprovedEnvelope(t *testing.T) {
	p := &goalServices{}
	_, catalog, err := capabilityagent.Catalogue(context.Background(), p)
	if err != nil {
		t.Fatal(err)
	}
	c := objective.Contract{SchemaVersion: objective.Version, RobotID: "local-test", Goal: "检查标定", CatalogRevision: capability.Fingerprint(catalog), Grants: []objective.Grant{{Tool: "calibration.get", Arguments: map[string]any{"type": "object", "additionalProperties": false}}}, Resources: []string{"robot"}, Effects: []string{"READ"}, Completion: []objective.Check{{Tool: "calibration.get", Arguments: map[string]any{}, Path: []string{"ready"}, Equals: true}}, Budget: objective.Budget{MaxPlans: 2, MaxSteps: 4, MaxReads: 8, TimeoutSeconds: 30}, ExpiresAt: time.Now().Add(time.Hour)}
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	handler := console.NewServer(service, nil, console.WithRobotServices(p), console.WithSessionToken(testSessionToken)).Handler()
	wire, _ := json.Marshal(map[string]any{"objective": c, "adapter": "ros2", "idempotencyKey": "local-request-001"})
	for _, authorized := range []bool{false, true} {
		q := httptest.NewRequest("POST", "http://localhost/v1/objectives", bytes.NewReader(wire))
		q.Header.Set("Content-Type", "application/json")
		if authorized {
			q.Header.Set(console.SessionHeaderName, testSessionToken)
		}
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, q)
		if !authorized {
			if response.Code < 400 {
				t.Fatal("missing session allowed")
			}
			continue
		}
		if response.Code != 201 {
			t.Fatalf("status=%d body=%s", response.Code, response.Body)
		}
		var task tasks.Task
		if err = json.Unmarshal(response.Body.Bytes(), &task); err != nil {
			t.Fatal(err)
		}
		if task.Approved || task.Plan.Capabilities.Calls[0].Tool != objective.Tool || p.calls != 0 {
			t.Fatal("objective creation planned or executed effects")
		}
	}
}
