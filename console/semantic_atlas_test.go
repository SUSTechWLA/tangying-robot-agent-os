package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

type atlasConsoleProvider struct {
	calls    []*robotv1.ServiceRequest
	conflict bool
}

func (*atlasConsoleProvider) ListServices(context.Context) (*robotv1.ServiceCatalog, error) {
	return &robotv1.ServiceCatalog{RobotId: "atlas-runtime", Services: []*robotv1.ServiceDefinition{
		{Name: "semantic.map", Available: true}, {Name: "semantic.query", Available: true},
		{Name: "semantic.plan", Available: true}, {Name: "semantic.annotate", Available: true, MutatesWorld: true},
	}}, nil
}

func (p *atlasConsoleProvider) CallService(_ context.Context, request *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error) {
	p.calls = append(p.calls, request)
	result, _ := structpb.NewStruct(map[string]any{"mapRevision": "geometry-1", "semanticRevision": "atlas-2"})
	if p.conflict {
		return &robotv1.ServiceResponse{Ok: false, Code: "SEMANTIC_REVISION_CONFLICT", Message: "semantic revision changed", Result: result}, nil
	}
	return &robotv1.ServiceResponse{Ok: true, Code: "OK", Result: result}, nil
}

func TestSemanticAtlasOperatorRoutePreservesCASAndRevisionConflict(t *testing.T) {
	for _, test := range []struct {
		name       string
		session    bool
		conflict   bool
		wantStatus int
	}{
		{"operator annotation", true, false, http.StatusOK},
		{"stale operator annotation", true, true, http.StatusConflict},
		{"annotation without operator session", false, false, http.StatusUnauthorized},
	} {
		t.Run(test.name, func(t *testing.T) {
			provider := &atlasConsoleProvider{conflict: test.conflict}
			handler := console.NewServer(nil, nil, console.WithRobotServices(provider), console.WithSessionToken(testSessionToken)).Handler()
			request := httptest.NewRequest(http.MethodPost, "http://localhost/v1/robot/services", strings.NewReader(
				`{"name":"semantic.annotate","requestId":"operator-atlas-1","parameters":{"mapId":"measured-home","mapRevision":"geometry-1","expectedSemanticRevision":"atlas-1","annotations":[{"id":"table-1","category":"table"}]}}`))
			request.Header.Set("Content-Type", "application/json")
			if test.session {
				request.Header.Set(console.SessionHeaderName, testSessionToken)
			}
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != test.wantStatus {
				t.Fatalf("response=%d body=%s", response.Code, response.Body.String())
			}
			if !test.session {
				if len(provider.calls) != 0 {
					t.Fatal("unauthenticated annotation reached runtime")
				}
				return
			}
			if len(provider.calls) != 1 {
				t.Fatalf("annotation dispatched %d times", len(provider.calls))
			}
			call := provider.calls[0]
			parameters := call.Parameters.AsMap()
			if call.RobotId != "atlas-runtime" || call.Name != "semantic.annotate" || call.RequestId != "operator-atlas-1" ||
				parameters["mapId"] != "measured-home" || parameters["mapRevision"] != "geometry-1" || parameters["expectedSemanticRevision"] != "atlas-1" {
				t.Fatalf("annotation lost runtime identity, request identity or compare-and-swap guard: %+v", call)
			}
			var receipt map[string]any
			if err := json.Unmarshal(response.Body.Bytes(), &receipt); err != nil {
				t.Fatal(err)
			}
			if receipt["result"].(map[string]any)["semanticRevision"] != "atlas-2" || receipt["ok"] != !test.conflict {
				t.Fatalf("runtime receipt was not preserved: %+v", receipt)
			}
			if test.conflict && receipt["code"] != "SEMANTIC_REVISION_CONFLICT" {
				t.Fatalf("revision conflict was hidden: %+v", receipt)
			}
		})
	}
}
