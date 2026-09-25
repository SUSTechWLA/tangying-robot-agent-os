package fleet

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/agentharness"
)

// SystemAgent uses the same decision loop as edge recovery, with a Fleet-only
// tool catalog. It may create an unapproved task draft but never dispatch or
// approve a physical task. Existing operator approval remains authoritative.
type SystemAgent struct {
	profile agentharness.Profile
	decider actionloop.Decider
}

func NewSystemAgent(profile agentharness.Profile, decider actionloop.Decider) (*SystemAgent, error) {
	if profile.Role() != agentharness.Server || decider == nil {
		return nil, errors.New("system agent requires a server harness and decider")
	}
	return &SystemAgent{profile: profile, decider: decider}, nil
}

func (s *Server) runSystemAgent(w http.ResponseWriter, r *http.Request) {
	principal, ok := auth.PrincipalFromContext(r.Context())
	if !ok || principal.Role != "operator" {
		writeError(w, http.StatusForbidden, "OPERATOR_REQUIRED", "system agent requires operator authentication")
		return
	}
	if s.systemAgent == nil {
		writeError(w, http.StatusServiceUnavailable, "SYSTEM_AGENT_UNAVAILABLE", "system agent model is not configured")
		return
	}
	const maxRequest = 8 << 10
	data, err := io.ReadAll(io.LimitReader(r.Body, maxRequest+1))
	if err != nil || len(data) > maxRequest {
		writeError(w, http.StatusRequestEntityTooLarge, "SYSTEM_AGENT_REQUEST_TOO_LARGE", "system agent request exceeds limit")
		return
	}
	var input struct {
		Goal string `json:"goal"`
	}
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil || strings.TrimSpace(input.Goal) == "" || len(input.Goal) > 2000 {
		writeError(w, http.StatusBadRequest, "INVALID_SYSTEM_GOAL", "a goal of at most 2000 bytes is required")
		return
	}
	if decoder.Decode(new(any)) != io.EOF {
		writeError(w, http.StatusBadRequest, "INVALID_SYSTEM_GOAL", "one JSON object is required")
		return
	}
	capabilities := s.systemCapabilities()
	outcome, err := s.systemAgent.profile.Run(r.Context(), strings.TrimSpace(input.Goal), agentharness.RunConfig{
		Decider: s.systemAgent.decider,
		Observe: func(context.Context) (actionloop.Observation, error) {
			return actionloop.Observation{Summary: "Fleet system task. Read current fleet facts through offered tools; creating a task draft never approves or dispatches it."}, nil
		},
		Capabilities: capabilities,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "SYSTEM_AGENT_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, outcome)
}

func (s *Server) systemCapabilities() []agentharness.Capability {
	read := func(name, description string, parameters []string, call func(context.Context, map[string]any) (actionloop.Result, error)) agentharness.Capability {
		return agentharness.Capability{Class: agentharness.FleetRead, Tool: actionloop.Tool{
			Name: name, Description: description, Parameters: parameters,
			SafetyLevel: skills.SafetyReadOnly, Call: call,
		}}
	}
	return []agentharness.Capability{
		read("fleet.devices.read", "List a bounded page of registered robots and online status", []string{"offset", "limit"}, s.readDevicesForAgent),
		read("fleet.tasks.read", "List a bounded page of task status and robot assignment", []string{"offset", "limit"}, s.readTasksForAgent),
		read("fleet.world.read", "Read authoritative world revision, health and optional robot state", []string{"robotId"}, s.readWorldForAgent),
		read("fleet.metrics.read", "Read orchestration metrics for fleet diagnosis", nil, s.readMetricsForAgent),
		{Class: agentharness.FleetDraft, Tool: actionloop.Tool{
			Name: "fleet.task_draft.create", Description: "Create a task requiring separate operator approval; never dispatch it", Parameters: []string{"request", "adapter"},
			SafetyLevel: skills.SafetyLocal, Call: s.createDraftForAgent,
		}},
	}
}

func (s *Server) readMetricsForAgent(ctx context.Context, _ map[string]any) (actionloop.Result, error) {
	if s.service == nil {
		return toolFailure("RPC_UNAVAILABLE", errors.New("task service is not configured"))
	}
	return actionloop.Result{Success: true, Message: "orchestration metrics", Detail: map[string]any{
		"metrics": s.service.OrchestrationMetrics(ctx),
	}}, nil
}

func page(arguments map[string]any) (int, int, error) {
	offset, limit := 0, 25
	for name, target := range map[string]*int{"offset": &offset, "limit": &limit} {
		if raw, ok := arguments[name]; ok {
			value, ok := raw.(string)
			if !ok {
				return 0, 0, errors.New(name + " must be a decimal string")
			}
			parsed, err := strconv.Atoi(value)
			if err != nil || parsed < 0 {
				return 0, 0, errors.New(name + " must be non-negative")
			}
			*target = parsed
		}
	}
	if limit < 1 || limit > 100 {
		return 0, 0, errors.New("limit must be 1..100")
	}
	return offset, limit, nil
}

func toolFailure(code string, err error) (actionloop.Result, error) {
	return actionloop.Result{Success: false, Code: code, Message: err.Error()}, nil
}

func (s *Server) readDevicesForAgent(ctx context.Context, arguments map[string]any) (actionloop.Result, error) {
	if s.registry == nil {
		return toolFailure("RPC_UNAVAILABLE", errors.New("device registry is not configured"))
	}
	offset, limit, err := page(arguments)
	if err != nil {
		return toolFailure("INVALID_ARGUMENT", err)
	}
	devices, err := s.registry.List(ctx)
	if err != nil {
		return toolFailure("RPC_UNAVAILABLE", err)
	}
	sort.Slice(devices, func(i, j int) bool { return devices[i].RobotID < devices[j].RobotID })
	if offset > len(devices) {
		offset = len(devices)
	}
	end := min(len(devices), offset+limit)
	items := make([]map[string]any, 0, end-offset)
	for _, device := range devices[offset:end] {
		items = append(items, map[string]any{"robotId": device.RobotID, "online": device.Online,
			"toolCatalogRevision": device.ToolCatalogRevision, "lastSeen": device.LastSeen})
	}
	return actionloop.Result{Success: true, Message: "registered robot page", Detail: map[string]any{
		"total": len(devices), "offset": offset, "items": items,
	}}, nil
}

func (s *Server) readTasksForAgent(ctx context.Context, arguments map[string]any) (actionloop.Result, error) {
	if s.service == nil {
		return toolFailure("RPC_UNAVAILABLE", errors.New("task service is not configured"))
	}
	offset, limit, err := page(arguments)
	if err != nil {
		return toolFailure("INVALID_ARGUMENT", err)
	}
	taskList, err := s.service.List(ctx)
	if err != nil {
		return toolFailure("RPC_UNAVAILABLE", err)
	}
	sort.Slice(taskList, func(i, j int) bool { return taskList[i].ID < taskList[j].ID })
	if offset > len(taskList) {
		offset = len(taskList)
	}
	end := min(len(taskList), offset+limit)
	items := make([]map[string]any, 0, end-offset)
	for _, task := range taskList[offset:end] {
		items = append(items, map[string]any{"taskId": task.ID, "state": task.State,
			"robots": intentsRobots(task), "request": truncate(task.Request, 160)})
	}
	return actionloop.Result{Success: true, Message: "task page", Detail: map[string]any{
		"total": len(taskList), "offset": offset, "items": items,
	}}, nil
}

func (s *Server) readWorldForAgent(ctx context.Context, arguments map[string]any) (actionloop.Result, error) {
	if s.world == nil {
		return toolFailure("RPC_UNAVAILABLE", errors.New("world reader is not configured"))
	}
	snapshot, err := s.world.Snapshot(ctx)
	if err != nil {
		return toolFailure("RPC_UNAVAILABLE", err)
	}
	detail := map[string]any{"worldId": snapshot.WorldID, "revision": snapshot.Revision,
		"projectedAt": snapshot.ProjectedAt, "health": snapshot.Health,
		"robotCount": len(snapshot.Robots), "entityCount": len(snapshot.Entities),
		"activeTasks": snapshot.ActiveTasks[:min(len(snapshot.ActiveTasks), 100)]}
	if robotID, _ := arguments["robotId"].(string); robotID != "" {
		robot, ok := snapshot.Robots[robotID]
		if !ok {
			return toolFailure("INVALID_ARGUMENT", errors.New("robot is absent from world snapshot"))
		}
		detail["robotId"] = robotID
		detail["robot"] = robot
	}
	return actionloop.Result{Success: true, Message: "authoritative world snapshot", Detail: detail}, nil
}

func (s *Server) createDraftForAgent(ctx context.Context, arguments map[string]any) (actionloop.Result, error) {
	if s.service == nil {
		return toolFailure("RPC_UNAVAILABLE", errors.New("task service is not configured"))
	}
	request, ok := arguments["request"].(string)
	request = strings.TrimSpace(request)
	if !ok || request == "" || len(request) > 2000 {
		return toolFailure("INVALID_ARGUMENT", errors.New("request must be 1..2000 bytes"))
	}
	adapter, _ := arguments["adapter"].(string)
	if len(adapter) > 80 {
		return toolFailure("INVALID_ARGUMENT", errors.New("adapter is too long"))
	}
	task, err := s.service.Create(ctx, request, adapter)
	if err != nil {
		return toolFailure("INVALID_ARGUMENT", err)
	}
	return actionloop.Result{Success: true, Message: "task draft created; separate operator approval is required", Detail: map[string]any{
		"taskId": task.ID, "state": task.State, "approvalRequired": true, "dispatched": false,
	}}, nil
}

func truncate(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}
