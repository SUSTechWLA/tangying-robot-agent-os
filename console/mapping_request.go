package console

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"strings"
)

// The natural-language front door for mapping.
//
// This is the route a person's sentence takes when they ask the robot for a map.
// It exists because the console's task grammar (`agent.Parser`) is a manipulation
// grammar: it knows objects, destinations and routes, and a request to survey a
// house has none of those. Rather than widen that grammar with a fourth action
// that grounds against an object it does not have, mapping gets its own entry
// point, and the decision it carries - reuse a map or survey one - is delegated to
// the robot, which is where the evidence is.
//
// What this route deliberately does **not** do is interpret the sentence beyond
// recognising that it is a mapping request. It cannot pick a room out of prose,
// because a wrong room name does not produce an error - `select_map` answers
// `ambiguous` and asks - so the only parameter it forwards is one the caller
// supplied explicitly. Guessing here would turn a question into a wrong map.

// mappingRequestVocabulary is the intent marker, and it is the same vocabulary the
// Python tool catalogue declares for `build_map`:
// "当用户要求探索环境 / 建图 / 构建全局地图 / 扫描这里 / 看看这地方长什么样时调用".
//
// Keeping one list on both sides is not possible across two languages, so this one
// is written to be *checked against* the other: a phrase that starts a survey here
// must be a phrase the tool layer also routes to `build_map`, and vice versa.
var mappingRequestVocabulary = []string{
	"探索", "建图", "构建地图", "构建全局地图", "建立地图", "建一张",
	"扫描", "扫图", "测绘", "地图", "看看这地方", "看看这里", "环境长什么样",
	"map the", "build a map", "explore", "survey", "scan the",
}

// surveyRequestNamesService reports whether a request is a mapping request, and
// which service should serve it.
//
// The order matters only in that `mapping.ensure` is preferred: it decides
// reuse-versus-survey itself and is the entry the tool layer names. A robot that
// does not serve it is not driven through `mapping.start` silently, because
// starting a survey is the one outcome with a cost - twenty minutes of a moving
// robot - and a caller should be told which one it is getting.
func surveyRequestNamesService(request string) string {
	return "mapping.ensure"
}

// newRequestID is the idempotency key for one console-issued robot operation. A
// random key is deliberate: the console has no natural identity for "the survey
// the operator asked for in this sentence", and reusing a counter would make two
// genuinely separate requests look like a replay of one.
func newRequestID() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		// A key that cannot be generated is a reason to refuse the operation, not a
		// reason to invent one: an unknown key replays somebody else's survey.
		return ""
	}
	return "console-mapping-" + hex.EncodeToString(buffer)
}

func isMappingRequest(request string) bool {
	lowered := strings.ToLower(request)
	for _, marker := range mappingRequestVocabulary {
		if strings.Contains(lowered, strings.ToLower(marker)) {
			return true
		}
	}
	return false
}

func (s *Server) mappingRequest(w http.ResponseWriter, r *http.Request) {
	if !s.allowOperatorWrite(w, r) {
		return
	}
	if s.robotServices == nil {
		writeError(w, http.StatusServiceUnavailable, "SERVICES_UNAVAILABLE",
			"机器人未连接服务目录。")
		return
	}
	var body struct {
		Request     string  `json:"request"`
		Environment string  `json:"environment"`
		MaxTravelM  float64 `json:"maxTravelM"`
		MaxLegs     int     `json:"maxLegs"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 200_000))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", err.Error())
		return
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "请求只能包含一个 JSON 对象。")
		return
	}
	if strings.TrimSpace(body.Request) == "" || len(body.Request) > 2000 {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "request is required")
		return
	}
	// A sentence this route does not recognise is refused rather than forwarded:
	// "put the cup away" reaching `mapping.ensure` would start a survey, and a
	// survey nobody asked for moves a robot through a house.
	if !isMappingRequest(body.Request) {
		writeError(w, http.StatusUnprocessableEntity, "UNSUPPORTED_INTENT",
			"这句话不是建图请求。要建图请说明探索、建图、扫描或构建地图。")
		return
	}

	parameters := map[string]any{}
	if name := strings.TrimSpace(body.Environment); name != "" {
		parameters["environment"] = name
	}
	if body.MaxTravelM > 0 {
		parameters["maxTravelM"] = body.MaxTravelM
	}
	if body.MaxLegs > 0 {
		parameters["maxLegs"] = body.MaxLegs
	}
	// `mapping.ensure` is a mutating service, and the runtime refuses one without
	// an idempotency key: a drive that a lost reply might have already started must
	// be replayable by identity rather than by guessing. The console is the client
	// here, so the console supplies the key - and it is generated per request, not
	// per route, because two identical sentences are two surveys the operator asked
	// for, not one asked twice.
	result, err := s.invokeRobotService(r.Context(), surveyRequestNamesService(body.Request),
		newRequestID(), parameters)
	if err != nil {
		writeError(w, http.StatusBadGateway, "MAPPING_UNAVAILABLE",
			"机器人没有响应建图请求。")
		return
	}
	if !result.Ok {
		writeError(w, http.StatusConflict, result.Code, result.Message)
		return
	}
	payload := result.Result.AsMap()
	// The sentence and the intent it was read as travel back with the answer, so a
	// caller can see that the route recognised a mapping request rather than
	// silently doing something else with the words.
	payload["request"] = body.Request
	payload["served"] = surveyRequestNamesService(body.Request)
	writeJSON(w, http.StatusOK, payload)
}
