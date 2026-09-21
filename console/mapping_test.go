package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	console "github.com/SUSTechWLA/tangying-robot-agent-os/console"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

// mappingProvider answers one service and remembers what it was asked for.
type mappingProvider struct {
	asked  []string
	ids    []string
	answer *robotv1.ServiceResponse
	err    error
}

func (p *mappingProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	return &robotv1.ServiceCatalog{RobotId: "unit-1"}, nil
}

func (p *mappingProvider) CallService(_ context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.asked = append(p.asked, request.Name)
	p.ids = append(p.ids, request.RequestId)
	if p.err != nil {
		return nil, p.err
	}
	return p.answer, nil
}

func surveyAnswer(t *testing.T, fields map[string]any) *robotv1.ServiceResponse {
	t.Helper()
	payload, err := structpb.NewStruct(fields)
	if err != nil {
		t.Fatalf("structpb.NewStruct: %v", err)
	}
	return &robotv1.ServiceResponse{Ok: true, Result: payload}
}

func TestMappingStatusIsReadableWithoutAnOperatorSession(t *testing.T) {
	// A survey moves a robot for twenty minutes. Watching one and starting one are
	// different questions, and this route answers only the second - which is why it
	// carries no session requirement.
	provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{
		"state": "exploring", "unknownFraction": 0.31, "stopReason": "",
	})}
	server := console.NewServer(nil, nil, console.WithRobotServices(provider)).Handler()
	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/v1/mapping", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", recorder.Code, recorder.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("body is not JSON: %v", err)
	}
	if body["state"] != "exploring" {
		t.Fatalf("state = %#v", body["state"])
	}
	if len(provider.asked) != 1 || provider.asked[0] != "mapping.status" {
		t.Fatalf("asked for %#v, want exactly mapping.status", provider.asked)
	}
	// The projection says what it is. A client must be able to tell that this is a
	// read-only view and not the robot's whole service surface.
	if body["readOnly"] != true || body["canStartSurvey"] != false {
		t.Fatalf("the projection did not declare itself: %#v", body)
	}
	if recorder.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("a live survey must not be served from a cache")
	}
}

func TestMappingStatusCannotReachAnyOtherService(t *testing.T) {
	// The safety property of the route, and the reason it is not a general service
	// call: the name is a literal, so no request can ask it for something else.
	provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{"state": "idle"})}
	server := console.NewServer(nil, nil, console.WithRobotServices(provider)).Handler()
	for _, path := range []string{
		"/v1/mapping?name=mapping.start",
		"/v1/mapping?service=mapping.finish",
	} {
		recorder := httptest.NewRecorder()
		server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusOK {
			t.Fatalf("%s: status = %d", path, recorder.Code)
		}
	}
	for _, name := range provider.asked {
		if name != "mapping.status" {
			t.Fatalf("the route invoked %q; it must only ever invoke mapping.status", name)
		}
	}
}

func TestMappingStatusReportsTheRobotsOwnRefusalRatherThanSuccess(t *testing.T) {
	provider := &mappingProvider{answer: &robotv1.ServiceResponse{
		Ok: false, Code: "MAPPING_IDLE", Message: "没有正在进行的扫描",
	}}
	server := console.NewServer(nil, nil, console.WithRobotServices(provider)).Handler()
	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/v1/mapping", nil))

	if recorder.Code == http.StatusOK {
		t.Fatal("a refusal must not be reported as success")
	}
	// The robot's own code travels: a caller has to be able to tell "no survey has
	// been started" from "the robot is unreachable".
	if !strings.Contains(recorder.Body.String(), "MAPPING_IDLE") {
		t.Fatalf("the robot's code was swallowed: %s", recorder.Body.String())
	}
}

func TestMappingStatusSaysSoWhenNoRobotIsConnected(t *testing.T) {
	server := console.NewServer(nil, nil).Handler()
	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/v1/mapping", nil))
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", recorder.Code)
	}
}
