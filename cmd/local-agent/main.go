package main

import (
	"context"
	"flag"
	"fmt"
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

type config struct {
	cloudURL        string
	robotAddress    string
	agentID         string
	dataDir         string
	devInsecure     bool
	once            bool
	robotCA         string
	robotCert       string
	robotKey        string
	robotServerName string
}

func parseConfig(arguments []string) (config, error) {
	var result config
	flags := flag.NewFlagSet("local-agent", flag.ContinueOnError)
	flags.StringVar(&result.cloudURL, "cloud", "http://127.0.0.1:8080", "cloud control plane URL")
	flags.StringVar(&result.robotAddress, "robot", "127.0.0.1:50051", "Robot Gateway gRPC address")
	flags.StringVar(&result.agentID, "agent-id", "mac-local-agent", "stable Local Agent identifier")
	flags.StringVar(&result.dataDir, "data-dir", defaultDataDir(), "Local Agent data directory")
	flags.BoolVar(&result.devInsecure, "dev-insecure", false, "allow plaintext Robot Gateway connection")
	flags.BoolVar(&result.once, "once", false, "claim at most one task and exit")
	flags.StringVar(&result.robotCA, "robot-ca", "", "Robot Gateway CA certificate")
	flags.StringVar(&result.robotCert, "robot-cert", "", "Local Agent client certificate")
	flags.StringVar(&result.robotKey, "robot-key", "", "Local Agent client private key")
	flags.StringVar(&result.robotServerName, "robot-server-name", "", "expected Robot Gateway TLS server name")
	if err := flags.Parse(arguments); err != nil {
		return config{}, fmt.Errorf("parse local agent flags: %w", err)
	}
	return result, nil
}

func main() {
	config, err := parseConfig(os.Args[1:])
	if err != nil {
		log.Fatal(err)
	}

	if err := os.MkdirAll(config.dataDir, 0o700); err != nil {
		log.Fatal(err)
	}
	store, err := localstore.Open(filepath.Join(config.dataDir, "agent.db"))
	if err != nil {
		log.Fatal(err)
	}
	defer store.Close()
	robot, err := robotclient.New(robotclient.Config{
		Address: config.robotAddress, DevInsecure: config.devInsecure,
		CAFile: config.robotCA, CertFile: config.robotCert, KeyFile: config.robotKey,
		ServerName: config.robotServerName,
	})
	if err != nil {
		log.Fatal(err)
	}
	defer robot.Close()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	cloud := cloudclient.New(config.cloudURL)
	runner := agent.NewRunner(store, robot)
	for {
		if err := runOnce(ctx, cloud, runner, config.agentID); err != nil {
			log.Printf("local agent cycle failed: %v", err)
		}
		if config.once {
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
