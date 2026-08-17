package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/cloudclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
)

func main() {
	cloudURL := flag.String("cloud", "http://127.0.0.1:8080", "cloud control plane URL")
	robotAddress := flag.String("robot", "127.0.0.1:50051", "Robot Gateway gRPC address")
	agentID := flag.String("agent-id", "mac-local-agent", "stable Local Agent identifier")
	dataDir := flag.String("data-dir", defaultDataDir(), "Local Agent data directory")
	devInsecure := flag.Bool("dev-insecure", false, "allow plaintext Robot Gateway connection")
	once := flag.Bool("once", false, "claim at most one task and exit")
	flag.Parse()

	if err := os.MkdirAll(*dataDir, 0o700); err != nil {
		log.Fatal(err)
	}
	store, err := localstore.Open(filepath.Join(*dataDir, "agent.db"))
	if err != nil {
		log.Fatal(err)
	}
	defer store.Close()
	robot, err := robotclient.New(robotclient.Config{Address: *robotAddress, DevInsecure: *devInsecure})
	if err != nil {
		log.Fatal(err)
	}
	defer robot.Close()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	cloud := cloudclient.New(*cloudURL)
	runner := agent.NewRunner(store, robot)
	for {
		if err := runOnce(ctx, cloud, runner, *agentID); err != nil {
			log.Printf("local agent cycle failed: %v", err)
		}
		if *once {
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}

func runOnce(ctx context.Context, cloud *cloudclient.Client, runner *agent.Runner, agentID string) error {
	claim, err := cloud.Claim(ctx, agentID)
	if err != nil || claim.Task == nil {
		return err
	}
	task := claim.Task
	if err := cloud.SetState(ctx, task.ID, "OBSERVING", "local agent claimed task"); err != nil {
		return err
	}
	result, err := runner.Run(ctx, task)
	if err != nil {
		_ = cloud.AppendEvent(ctx, task.ID, orchestrator.TaskEvent{Type: "LOCAL_RUN_FAILED", Message: err.Error()})
		return err
	}
	if err := cloud.SetState(ctx, task.ID, "PLANNING", "grounding and plan completed"); err != nil {
		return err
	}
	if err := cloud.SetState(ctx, task.ID, "EXECUTING", "physical skills completed locally"); err != nil {
		return err
	}
	if err := cloud.SetState(ctx, task.ID, "VERIFYING", "post-action verification completed"); err != nil {
		return err
	}
	if err := cloud.SetState(ctx, task.ID, "SUCCEEDED", "closed-loop task succeeded"); err != nil {
		return err
	}
	return cloud.AppendEvent(ctx, task.ID, orchestrator.TaskEvent{Type: "LOCAL_RUN_SUCCEEDED", Payload: map[string]any{"completedSteps": result.CompletedSteps}})
}

func defaultDataDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ".tangying-robot-agent"
	}
	return filepath.Join(home, "Library", "Application Support", "TangyingRobotAgent")
}

func init() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
}
