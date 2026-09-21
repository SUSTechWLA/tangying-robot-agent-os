package console

import "net/http"

// The survey's read-only projection.
//
// A survey moves a robot autonomously for twenty minutes, so who may *start* one
// is a different question from who may *watch* one. This route answers only the
// second: the service name is a literal rather than a parameter, exactly as
// `navigation.map` and `calibration.get` are, so this handler cannot be turned
// into a way to invoke something else.
//
// That restriction is the point rather than an implementation detail. The
// guarded write route (`POST /v1/robot/services`) needs an operator's console
// session, which an agent-authenticated client does not have and must not be
// given; this route needs nothing, and in exchange it can reach exactly one
// read-only service. An agent can therefore see what a survey is doing, how much
// of the house it has measured and why it stopped, and cannot begin, extend or
// end one.
func (s *Server) mappingStatus(w http.ResponseWriter, r *http.Request) {
	// A survey's progress is only interesting live; a cached copy would show an
	// operator a map that stopped moving minutes ago.
	w.Header().Set("Cache-Control", "no-store")
	if s.robotServices == nil {
		writeError(w, http.StatusServiceUnavailable, "SERVICES_UNAVAILABLE",
			"机器人未连接服务目录。")
		return
	}
	result, err := s.invokeRobotService(r.Context(), "mapping.status", "", nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, "MAPPING_STATUS_UNAVAILABLE",
			"读取扫描状态失败。")
		return
	}
	if !result.Ok {
		// The robot answered and said no. Passing its code through is what lets a
		// caller tell "no survey has been started" from "the robot is unreachable".
		writeError(w, http.StatusBadGateway, result.Code, result.Message)
		return
	}
	payload := result.Result.AsMap()
	// Stated rather than implied: a client reading this must be able to tell that
	// it is looking at a projection, not at the robot's whole service surface.
	payload["readOnly"] = true
	payload["canStartSurvey"] = false
	writeJSON(w, http.StatusOK, payload)
}
