package cloudclient_test

import (
	"context"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/api"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
)

func TestClientClaimsPersistedTask(t *testing.T) {
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
	created, _ := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	server := httptest.NewServer(api.NewServer(service).Handler())
	defer server.Close()

	claim, err := cloudclient.New(server.URL).Claim(context.Background(), "agent-1")
	if err != nil {
		t.Fatal(err)
	}
	if claim.Task == nil || claim.Task.ID != created.ID {
		t.Fatalf("claim = %+v", claim)
	}
	if err := cloudclient.New(server.URL).SetState(context.Background(), created.ID, "OBSERVING", "claimed"); err != nil {
		t.Fatal(err)
	}
	updated, _ := service.Get(context.Background(), created.ID)
	if updated.State != "OBSERVING" {
		t.Fatalf("state = %s", updated.State)
	}
}
