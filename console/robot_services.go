package console

import (
	"context"
	"encoding/json"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"

	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"google.golang.org/protobuf/types/known/structpb"
)

type RobotServiceProvider interface {
	ListServices(context.Context) (*robotv1.ServiceCatalog, error)
	CallService(context.Context, *robotv1.ServiceRequest) (*robotv1.ServiceResponse, error)
}

func WithRobotServices(provider RobotServiceProvider) Option {
	return func(s *Server) { s.robotServices = provider }
}

func (s *Server) serviceCatalogue(w http.ResponseWriter, r *http.Request) {
	if s.robotServices == nil {
		writeError(w, 503, "SERVICES_UNAVAILABLE", "机器人未连接服务目录。")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	catalog, err := s.robotServices.ListServices(ctx)
	if err != nil {
		writeError(w, 502, "SERVICES_UNAVAILABLE", err.Error())
		return
	}
	entries := make([]map[string]any, 0, len(catalog.Services))
	for _, item := range catalog.Services {
		entries = append(entries, map[string]any{"name": item.Name, "description": item.Description, "inputSchema": item.InputSchema.AsMap(), "available": item.Available, "mutatesWorld": item.MutatesWorld})
	}
	writeJSON(w, 200, map[string]any{"robotId": catalog.RobotId, "services": entries})
}

func (s *Server) callRobotService(w http.ResponseWriter, r *http.Request) {
	media, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	origin := r.Header.Get("Origin")
	if media != "application/json" || (origin != "" && origin != "http://"+r.Host && origin != "https://"+r.Host) || strings.EqualFold(r.Header.Get("Sec-Fetch-Site"), "cross-site") {
		writeError(w, http.StatusForbidden, "SERVICE_ORIGIN_REJECTED", "机器人操作必须来自当前工作台的 JSON 请求。")
		return
	}
	if s.robotServices == nil {
		writeError(w, 503, "SERVICES_UNAVAILABLE", "机器人未连接服务目录。")
		return
	}
	var body struct {
		Name       string         `json:"name"`
		RequestID  string         `json:"requestId"`
		Parameters map[string]any `json:"parameters"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2_000_000))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&body); err != nil {
		writeError(w, 400, "INVALID_REQUEST", err.Error())
		return
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		writeError(w, 400, "INVALID_REQUEST", "请求只能包含一个 JSON 对象。")
		return
	}
	if body.Name == "" || len(body.Name) > 100 || len(body.RequestID) > 128 {
		writeError(w, 400, "INVALID_REQUEST", "服务名称或操作编号无效。")
		return
	}
	result, err := s.invokeRobotService(r.Context(), body.Name, body.RequestID, body.Parameters)
	if err != nil {
		writeError(w, 502, "SERVICE_UNAVAILABLE", err.Error())
		return
	}
	status := http.StatusOK
	if !result.Ok {
		status = http.StatusConflict
	}
	writeJSON(w, status, map[string]any{"ok": result.Ok, "code": result.Code, "message": result.Message, "result": result.Result.AsMap()})
}

func (s *Server) invokeRobotService(ctx context.Context, name, id string, parameters map[string]any) (*robotv1.ServiceResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	catalog, err := s.robotServices.ListServices(ctx)
	if err != nil {
		return nil, err
	}
	values, err := structpb.NewStruct(parameters)
	if err != nil {
		return nil, err
	}
	return s.robotServices.CallService(ctx, &robotv1.ServiceRequest{RobotId: catalog.RobotId, Name: name, RequestId: id, Parameters: values})
}
