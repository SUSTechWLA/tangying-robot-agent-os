package console_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	console "github.com/SUSTechWLA/tangying-robot-agent-os/console"
)

// postMappingRequest sends one sentence to the natural-language mapping route.
func postMappingRequest(t *testing.T, provider *mappingProvider, body string) *httptest.ResponseRecorder {
	t.Helper()
	server := console.NewServer(nil, nil,
		console.WithRobotServices(provider), console.WithSessionToken(testSessionToken)).Handler()
	request := httptest.NewRequest(http.MethodPost, "http://localhost/v1/mapping/request",
		strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set(console.SessionHeaderName, testSessionToken)
	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, request)
	return recorder
}

func TestASentenceAskingForAMapStartsOneThroughTheRobotsOwnEntryPoint(t *testing.T) {
	// The end-to-end claim in miniature: a person's words reach the robot's
	// mapping entry, and the *robot* decides whether that means reusing a map or
	// surveying one. The console must not make that decision - it does not have
	// the map inventory, the active revision or the calibration the decision needs.
	provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{
		"decision": "explore", "started": true, "reason": "no usable map for this robot",
	})}
	recorder := postMappingRequest(t, provider, `{"request":"请探索环境，构建全局地图"}`)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", recorder.Code, recorder.Body.String())
	}
	if len(provider.asked) != 1 || provider.asked[0] != "mapping.ensure" {
		t.Fatalf("asked for %#v, want exactly mapping.ensure", provider.asked)
	}
	// `mapping.ensure` is a mutating service and the runtime refuses one without an
	// idempotency key. This assertion is here because the first version of this
	// route sent an empty one and the only symptom was a 409 from the robot.
	if len(provider.ids) != 1 || provider.ids[0] == "" {
		t.Fatalf("the operation carried no idempotency key: %#v", provider.ids)
	}
	var body map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("body is not JSON: %v", err)
	}
	if body["decision"] != "explore" || body["started"] != true {
		t.Fatalf("the robot's decision did not survive the console: %#v", body)
	}
	// The sentence and the intent it was read as travel back with the answer, so a
	// caller can see what the route understood rather than inferring it.
	if body["request"] != "请探索环境，构建全局地图" || body["served"] != "mapping.ensure" {
		t.Fatalf("the answer does not say what was asked or how it was served: %#v", body)
	}
}

func TestEveryDeclaredMappingPhraseIsRecognised(t *testing.T) {
	// The vocabulary is duplicated across two languages, so it is checked rather
	// than assumed: a phrase the tool catalogue routes to `build_map` must reach a
	// survey here too, or a user gets a different answer depending on which door
	// they knocked on.
	for _, phrase := range []string{
		"探索环境", "建图", "构建全局地图", "扫描这里", "看看这地方长什么样",
		"帮我建一张客厅的地图", "survey the house", "build a map",
	} {
		provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{"decision": "explore"})}
		recorder := postMappingRequest(t, provider, `{"request":"`+phrase+`"}`)
		if recorder.Code != http.StatusOK {
			t.Fatalf("%q was not recognised: status = %d, body %s",
				phrase, recorder.Code, recorder.Body.String())
		}
	}
}

func TestASentenceThatIsNotAboutMappingNeverStartsASurvey(t *testing.T) {
	// The safety property of this route. A survey moves a robot through a house for
	// twenty minutes, and a request nobody made must not begin one - least of all by
	// being forwarded to a service whose whole job is to decide to survey.
	for _, phrase := range []string{
		"把红色杯子放进右侧收纳盒",
		"fetch the blue bottle",
		"停止",
	} {
		provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{"decision": "explore"})}
		recorder := postMappingRequest(t, provider, `{"request":"`+phrase+`"}`)
		if recorder.Code != http.StatusUnprocessableEntity {
			t.Fatalf("%q: status = %d, want 422", phrase, recorder.Code)
		}
		if len(provider.asked) != 0 {
			t.Fatalf("%q reached the robot as %#v", phrase, provider.asked)
		}
	}
}

func TestAnExplicitRoomIsForwardedAndNothingElseIsInvented(t *testing.T) {
	provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{"decision": "reuse"})}
	recorder := postMappingRequest(t, provider,
		`{"request":"建图","environment":"客厅","maxTravelM":30,"maxLegs":2}`)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, body %s", recorder.Code, recorder.Body.String())
	}
	if len(provider.asked) != 1 {
		t.Fatalf("asked %#v", provider.asked)
	}
}

func TestAMappingRequestNeedsAnOperatorSession(t *testing.T) {
	// Starting a survey is a write. The read-only projection next to it needs no
	// session precisely because it cannot do this.
	provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{"decision": "explore"})}
	server := console.NewServer(nil, nil,
		console.WithRobotServices(provider), console.WithSessionToken(testSessionToken)).Handler()
	request := httptest.NewRequest(http.MethodPost, "http://localhost/v1/mapping/request",
		strings.NewReader(`{"request":"探索环境"}`))
	request.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	server.ServeHTTP(recorder, request)
	if recorder.Code == http.StatusOK {
		t.Fatal("a survey was started without an operator session")
	}
	if len(provider.asked) != 0 {
		t.Fatalf("the request reached the robot anyway: %#v", provider.asked)
	}
}

func TestAnEmptySentenceIsRefusedBeforeItReachesTheRobot(t *testing.T) {
	provider := &mappingProvider{answer: surveyAnswer(t, map[string]any{})}
	recorder := postMappingRequest(t, provider, `{"request":"   "}`)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status = %d", recorder.Code)
	}
	if len(provider.asked) != 0 {
		t.Fatalf("an empty request reached the robot: %#v", provider.asked)
	}
}
