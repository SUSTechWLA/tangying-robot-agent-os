package console_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

type robotServices struct{ calls []*robotv1.ServiceRequest }

func (p *robotServices) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	return &robotv1.ServiceCatalog{RobotId: "ordinary-unit", Services: []*robotv1.ServiceDefinition{{Name: "calibration.get", Available: true}}}, nil
}
func (p *robotServices) CallService(_ context.Context, r *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.calls = append(p.calls, r)
	result, _ := structpb.NewStruct(map[string]any{"available": true, "revision": "revision"})
	return &robotv1.ServiceResponse{Ok: true, Code: "OK", Result: result}, nil
}

func TestRobotServicesUseRuntimeIdentityAndPreserveReceipt(t *testing.T) {
	provider := &robotServices{}
	handler := console.NewServer(nil, nil, console.WithRobotServices(provider)).Handler()
	request := httptest.NewRequest(http.MethodPost, "http://localhost/v1/robot/services", strings.NewReader(`{"name":"mapping.move","requestId":"deliberate-1","parameters":{"action":"forward"}}`))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 200 || len(provider.calls) != 1 {
		t.Fatalf("response %d: %s", response.Code, response.Body.String())
	}
	call := provider.calls[0]
	if call.RobotId != "ordinary-unit" || call.RequestId != "deliberate-1" || call.Parameters.AsMap()["action"] != "forward" {
		t.Fatalf("wrong request: %v", call)
	}
}

func TestRobotServicesRejectCrossOriginAndAmbiguousJSON(t *testing.T) {
	provider := &robotServices{}
	handler := console.NewServer(nil, nil, console.WithRobotServices(provider)).Handler()
	for _, test := range []struct {
		body, origin, content string
		status                int
	}{
		{`{"name":"mapping.start"}`, "https://untrusted.example", "application/json", 403},
		{`{"name":"mapping.start"}`, "", "text/plain", 403},
		{`{"name":"mapping.start","extra":true}`, "", "application/json", 400},
		{`{"name":"mapping.start"}{}`, "", "application/json", 400},
	} {
		request := httptest.NewRequest(http.MethodPost, "http://localhost/v1/robot/services", strings.NewReader(test.body))
		request.Header.Set("Content-Type", test.content)
		request.Header.Set("Origin", test.origin)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != test.status {
			t.Errorf("got %d want %d: %s", response.Code, test.status, response.Body.String())
		}
	}
	if len(provider.calls) != 0 {
		t.Fatal("rejected request reached robot")
	}
}

func TestCalibrationReadUsesRegisteredService(t *testing.T) {
	provider := &robotServices{}
	handler := console.NewServer(nil, nil, console.WithRobotServices(provider)).Handler()
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "/v1/calibration", nil))
	if response.Code != 200 || len(provider.calls) != 1 || provider.calls[0].Name != "calibration.get" {
		t.Fatalf("%d: %s", response.Code, response.Body.String())
	}
}
