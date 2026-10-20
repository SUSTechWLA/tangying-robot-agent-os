// UI integration fixture. It never connects to a Robot Runtime or executes a
// physical action. Run: go run ./console/testdata/workspace-preview
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type recordingExecutor struct{}

func (recordingExecutor) Enqueue(string) error { return errors.New("UI fixture: execution disabled") }
func (recordingExecutor) Cancel(string) error  { return errors.New("UI fixture: execution disabled") }

type previewRuntime struct{ id string }

func (r previewRuntime) Info(context.Context) (runtime.Snapshot, error) {
	return runtime.Snapshot{RobotID: r.id, Adapter: "ui-fixture", Ready: false,
		Blockers: []string{"界面验收样例：没有连接物理机器人"}, RuntimeVersion: "UI fixture"}, nil
}

func service(robot string) *tasks.Service {
	store := tasks.NewMemoryStore()
	for i, item := range []struct {
		state   taskgraph.TaskState
		request string
	}{
		{"SUCCEEDED", "【界面样例】完成环境扫描并保存观测"},
		{"WAITING_USER", "【界面样例】检查通道阻塞，等待人工核对"},
		{"AWAITING_APPROVAL", "【界面样例】规划仓库巡检路线"},
	} {
		now := time.Now().Add(-time.Duration(i+1) * time.Hour)
		err := store.Create(context.Background(), &tasks.Task{ID: fmt.Sprintf("task-preview-%d", i),
			Request: robot + " " + item.request, Adapter: "ui-fixture", State: item.state,
			CreatedAt: now, UpdatedAt: now,
			Events: []tasks.TaskEvent{{Sequence: 1, Type: "STATE_CHANGED", Message: robot + " 界面样例事件；没有物理执行", OccurredAt: now}},
		})
		if err != nil {
			log.Fatal(err)
		}
	}
	return tasks.NewService(store, intent.NewDeterministicParser())
}

func main() {
	dir, err := os.MkdirTemp("", "edge-workspaces-preview-")
	if err != nil {
		log.Fatal(err)
	}
	defer os.RemoveAll(dir)
	var servers []*http.Server
	start := func(handler http.Handler) string {
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			log.Fatal(err)
		}
		server := &http.Server{Handler: handler, ReadHeaderTimeout: 5 * time.Second}
		servers = append(servers, server)
		go server.Serve(listener)
		return "http://" + listener.Addr().String()
	}
	var profiles []map[string]string
	for _, row := range []struct{ id, robot, label string }{
		{"uav", "preview-uav", "无人机 · 界面样例"}, {"legged", "preview-legged", "足式 · Runtime 离线样例"},
	} {
		options := []console.Option{console.WithWorkspaceIdentity(row.robot)}
		if row.id != "legged" {
			options = append(options, console.WithRuntime(previewRuntime{row.robot}))
		}
		server := console.NewServer(service(row.robot), recordingExecutor{}, options...)
		token := filepath.Join(dir, row.id+"-session")
		if err := os.WriteFile(token, []byte(server.SessionToken()), 0600); err != nil {
			log.Fatal(err)
		}
		profiles = append(profiles, map[string]string{"id": row.id, "robotId": row.robot, "label": row.label,
			"url": start(server.Handler()), "sessionFile": token})
	}
	// An unreachable agent must remain selectable, with no fallback to the root.
	profiles = append(profiles, map[string]string{"id": "offline", "robotId": "preview-offline",
		"label": "Agent 离线样例", "url": "http://127.0.0.1:1", "sessionFile": filepath.Join(dir, "missing")})
	raw, _ := json.Marshal(map[string]any{"schemaVersion": "edge.workspaces.v1", "workspaces": profiles})
	config := filepath.Join(dir, "workspaces.json")
	if err := os.WriteFile(config, raw, 0600); err != nil {
		log.Fatal(err)
	}
	registry, err := console.LoadWorkspacesFile(config)
	if err != nil {
		log.Fatal(err)
	}
	server := console.NewServer(service("preview-agv"), recordingExecutor{}, console.WithWorkspaceIdentity("preview-agv"),
		console.WithRuntime(previewRuntime{"preview-agv"}), console.WithWorkspaces(registry))
	fmt.Printf("UI integration fixture (no robot control): %s\n", start(server.Handler()))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	<-ctx.Done()
	for _, server := range servers {
		server.Close()
	}
}
