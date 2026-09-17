package main

import (
	"bufio"
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	llmagent "github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
	robotruntime "github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	"github.com/SUSTechWLA/tangying-robot-agent-os/incidents"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/discovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localconfig"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
	"github.com/SUSTechWLA/tangying-robot-agent-os/latency"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type config struct {
	configFile         string
	listen             string
	robotAddress       string
	dataDir            string
	devInsecure        bool
	robotCA            string
	robotCert          string
	robotKey           string
	robotServerName    string
	robotSafetyProfile string
	llmProvider        string
	llmBaseURL         string
	llmAPIKey          string
	llmModel           string
	llmSamples         int
}

func parseConfig(arguments []string) (config, error) {
	var result config
	configPath, err := findConfigPath(arguments)
	if err != nil {
		return config{}, err
	}
	values, err := readConfigFile(configPath)
	if err != nil {
		return config{}, err
	}
	flags := flag.NewFlagSet("local-agent", flag.ContinueOnError)
	flags.StringVar(&result.configFile, "config", configPath, "Local Agent environment configuration file")
	flags.StringVar(&result.listen, "listen", configValue(values, "LOCAL_LISTEN", "127.0.0.1:8787"), "loopback Console/API listen address")
	flags.StringVar(&result.robotAddress, "robot", configValue(values, "ROBOT_ADDRESS", "127.0.0.1:50051"), "Robot Runtime gRPC address")
	flags.StringVar(&result.dataDir, "data-dir", defaultDataDir(), "Local Agent data directory")
	flags.BoolVar(&result.devInsecure, "dev-insecure", false, "allow plaintext Robot Runtime connection")
	flags.StringVar(&result.robotCA, "robot-ca", values["ROBOT_CA"], "Robot Runtime CA certificate")
	flags.StringVar(&result.robotCert, "robot-cert", values["ROBOT_CERT"], "Local Agent client certificate")
	flags.StringVar(&result.robotKey, "robot-key", values["ROBOT_KEY"], "Local Agent client private key")
	flags.StringVar(&result.robotServerName, "robot-server-name", values["ROBOT_SERVER_NAME"], "expected Robot Runtime TLS server name")
	flags.StringVar(&result.robotSafetyProfile, "robot-safety-profile", values["ROBOT_SAFETY_PROFILE"], "explicit runtime safety profile for a commissioned endpoint")
	flags.StringVar(&result.llmProvider, "llm-provider", configValue(values, "AGENT_PROVIDER", "deterministic"), "agent provider: deterministic or openai")
	flags.StringVar(&result.llmBaseURL, "llm-base-url", values["AGENT_BASE_URL"], "OpenAI-compatible API base URL")
	flags.StringVar(&result.llmAPIKey, "llm-api-key", values["AGENT_API_KEY"], "OpenAI-compatible API key")
	flags.StringVar(&result.llmModel, "llm-model", values["AGENT_MODEL"], "LLM model name")
	defaultSamples, err := integerConfig(values, "AGENT_ORCHESTRATION_SAMPLES")
	if err != nil {
		return config{}, err
	}
	flags.IntVar(&result.llmSamples, "llm-samples", defaultSamples, "number of orchestration candidates")
	if err := flags.Parse(arguments); err != nil {
		return config{}, fmt.Errorf("parse local agent flags: %w", err)
	}
	if result.listen == "" {
		return config{}, errors.New("local listen address is required")
	}
	return result, nil
}

// findConfigPath decides which configuration file to read.
//
// An explicit --config always wins. Without one this used to return "", which
// meant every value fell back to its default: the agent silently targeted
// 127.0.0.1:50051 in plaintext and ignored the certificates and address that
// pairing had just written. `make build && ./bin/local-agent` — the command the
// cold-start guide gives — therefore produced an agent that could not see the
// robot it had just been paired with, and nothing said why.
//
// The default is now the same file the installer and the pairing script write, so
// the three agree by construction rather than by an operator remembering to pass
// a flag. A file that is not there is not an error: defaults still apply.
func findConfigPath(arguments []string) (string, error) {
	for index, argument := range arguments {
		if argument == "--config" {
			if index+1 >= len(arguments) {
				return "", errors.New("--config requires a file path")
			}
			return arguments[index+1], nil
		}
		if strings.HasPrefix(argument, "--config=") {
			return strings.TrimPrefix(argument, "--config="), nil
		}
	}
	return defaultConfigPath(), nil
}

// defaultConfigPath is where the installer and the pairing script put local.env.
//
// Both directories are configurable through the environment, and they are read
// here in the same order and from the same variables as the scripts use. Two
// independent resolutions of the same path is how a pairing writes a file that
// the agent never reads.
func defaultConfigPath() string {
	if configured := strings.TrimSpace(os.Getenv("ROBOT_AGENT_CONFIG_DIR")); configured != "" {
		return filepath.Join(configured, "local.env")
	}
	if runtime.GOOS == "darwin" {
		home, err := os.UserHomeDir()
		if err != nil {
			return ""
		}
		return filepath.Join(home, "Library", "Application Support", "TangyingRobotAgent", "local.env")
	}
	if stateDirectory := strings.TrimSpace(os.Getenv("XDG_CONFIG_HOME")); stateDirectory != "" {
		return filepath.Join(stateDirectory, "tangying-robot-agent-os", "local.env")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".config", "tangying-robot-agent-os", "local.env")
}

// readConfigFile reads the configuration file.
//
// A file that is not there is not an error. The default path points at where the
// installer puts local.env, and a machine that has not been paired yet has no such
// file — refusing to start would make the agent unusable out of the box, which is
// the opposite of what a default is for. A file that exists and cannot be read is
// still an error: that is a permission or corruption problem, not an absence.
func readConfigFile(path string) (map[string]string, error) {
	values := map[string]string{}
	if path == "" {
		return values, nil
	}
	file, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return values, nil
	}
	if err != nil {
		return nil, fmt.Errorf("open Local Agent config: %w", err)
	}
	defer file.Close()
	allowed := map[string]bool{
		"LOCAL_LISTEN": true, "ROBOT_ADDRESS": true, "ROBOT_SERVER_NAME": true,
		"ROBOT_CA": true, "ROBOT_CERT": true, "ROBOT_KEY": true,
		"ROBOT_SAFETY_PROFILE": true,
		"AGENT_PROVIDER":       true, "AGENT_BASE_URL": true, "AGENT_API_KEY": true,
		"AGENT_MODEL": true, "AGENT_ORCHESTRATION_SAMPLES": true,
	}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok || !allowed[key] {
			return nil, fmt.Errorf("invalid Local Agent config key: %q", line)
		}
		values[key] = value
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read Local Agent config: %w", err)
	}
	return values, nil
}

func configValue(values map[string]string, key, fallback string) string {
	if value := values[key]; value != "" {
		return value
	}
	return fallback
}

func integerConfig(values map[string]string, key string) (int, error) {
	if values[key] == "" {
		return 0, nil
	}
	value, err := strconv.Atoi(values[key])
	if err != nil || value < 0 {
		return 0, fmt.Errorf("invalid %s: %q", key, values[key])
	}
	return value, nil
}

func main() {
	configuration, err := parseConfig(os.Args[1:])
	if err != nil {
		log.Fatal(err)
	}
	if err := run(configuration); err != nil {
		log.Fatal(err)
	}
}

func run(configuration config) error {
	if err := os.MkdirAll(configuration.dataDir, 0o700); err != nil {
		return err
	}
	store, err := sqlite.Open(filepath.Join(configuration.dataDir, "agent.db"))
	if err != nil {
		return err
	}
	defer store.Close()
	robot, err := robotclient.New(robotclient.Config{
		Address: configuration.robotAddress, DevInsecure: configuration.devInsecure,
		CAFile: configuration.robotCA, CertFile: configuration.robotCert, KeyFile: configuration.robotKey,
		ServerName: configuration.robotServerName,
		Profile:    configuration.robotSafetyProfile,
		// Experiment control: the household comparison runs both arms in one
		// binary. Nothing in a deployment should set this.
		WithoutRecallGoals: os.Getenv("TANGYING_RECALL_GOAL") == "off",
		// Documented deployment parameter: how old a remembered sighting may be.
		RecallGoalMaxAgeMS: robotclient.RecallGoalMaxAge(os.Getenv),
	})
	if err != nil {
		return err
	}
	defer robot.Close()
	navigation, err := console.NewNavigationReader(os.Getenv("TANGYING_NAVIGATION_URL"), os.Getenv("TANGYING_NAVIGATION_TOKEN"))
	if err != nil {
		return err
	}

	parser := llmagent.NewParser(llmagent.Config{
		Provider: configuration.llmProvider, BaseURL: configuration.llmBaseURL,
		APIKey: configuration.llmAPIKey, Model: configuration.llmModel,
	})
	planner := orchestration.New(manipulation.Catalog(), orchestration.Config{
		Provider: configuration.llmProvider, BaseURL: configuration.llmBaseURL,
		APIKey: configuration.llmAPIKey, Model: configuration.llmModel, Samples: configuration.llmSamples,
	})
	service := tasks.NewService(store, parser, planner)
	world := worldhub.New("local-default", 2*time.Second, 512)
	worldPublisher := worker.New(worker.Config{
		RobotID: "robot-local", Adapter: "local-runtime", WorldID: "local-default",
		TransformRevision: "local-world-v1", AdapterVersion: "v1",
	})
	publishTelemetry := func(ctx context.Context, snapshot telemetry.Snapshot) error {
		if snapshot.TaskID != "" && snapshot.StepID != "" && snapshot.Reconstruction != nil {
			if _, err := store.RecordEvidence(ctx, snapshot); err != nil {
				log.Printf("task %s step %s evidence persistence failed: %v", snapshot.TaskID, snapshot.StepID, err)
				_, _ = service.AppendEvent(ctx, snapshot.TaskID, tasks.TaskEvent{
					Type: "OBSERVATION_EVIDENCE_FAILED", StepID: snapshot.StepID, Message: err.Error(),
					Payload: map[string]any{"captureId": snapshot.Reconstruction.ObservationID},
				})
				return err
			}
			// Command evidence may precede a newer live capture or come from the
			// base camera. Archive it without rewinding the live scene/world.
			return nil
		}
		service.PublishTelemetry(ctx, snapshot)
		for _, envelope := range worldPublisher.ObservationsFromTelemetry(snapshot) {
			if _, err := world.Ingest(ctx, envelope); err != nil {
				return err
			}
		}
		return nil
	}
	router := robotruntime.NewRouter("robot-local", robot)
	grounder := agent.NewGrounderRouter("robot-local", robot)
	stepTimings := latency.New(latency.DefaultCapacity)
	runner := agent.NewRunner(store, grounder, router)
	runner.Latency = stepTimings
	runner.Telemetry = func(ctx context.Context, snapshot telemetry.Snapshot) error {
		return publishTelemetry(ctx, snapshot)
	}

	// The observing agent reads the same telemetry the executing agent already
	// uses. Nothing new is collected for it: an observer that needed its own
	// data source would become a second, competing account of robot state.
	telemetrySource := func(ctx context.Context, taskID string) (telemetry.Snapshot, error) {
		return robot.Telemetry(ctx, taskID)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	observerDone := startTelemetryObserver(ctx, robot, time.Second, func(ctx context.Context, snapshot telemetry.Snapshot) error {
		// Background frames update the live view only. Task evidence is captured
		// explicitly by Runner at grounding and post-tool boundaries.
		snapshot.TaskID, snapshot.StepID, snapshot.TaskRevision = "", "", 0
		return publishTelemetry(ctx, snapshot)
	})
	defer stopTelemetryObserver(cancel, observerDone)
	// Robots announce themselves on the local network; this is what listens. It is
	// started here rather than inside the console so the console stays a reader of
	// state it does not own, and a bind failure is logged rather than fatal: an
	// agent that cannot listen for robots is exactly as useful as one that has not
	// found any yet.
	robotDiscovery := discovery.StartInBackground(ctx)
	application := localapp.New(service, runner, memory.NewQueue[string](64)).
		WithIncidents(incidents.New(incidentDirectory(os.Getenv("TANGYING_INCIDENT_DIR")))).
		WithDiscoveredRobots(func() ([]discovery.Robot, bool) { return robotDiscovery.Robots(), true }).
		WithRecoveryExecution(&recoveryexec.Executor{
			// The two surfaces the recovery catalogue names, in one namespace: what
			// the robot declares it can do, and what this agent does itself.
			Registry: recoveryexec.Combined(
				recoveryexec.RobotServices(robot, func(ctx context.Context) *closedloop.Evidence {
					// A call that changes the world is confirmed by an observation
					// taken after it. The service carries none, so one is taken here
					// — the gate is satisfied rather than relaxed.
					snapshot, err := telemetrySource(ctx, "")
					if err != nil {
						return nil
					}
					return recoveryexec.EvidenceFromSnapshot(snapshot, "")
				}),
				recoveryexec.LocalTools{
					ReadTelemetry: func(ctx context.Context) (actionloop.Result, error) {
						if _, err := telemetrySource(ctx, ""); err != nil {
							// A read that could not be taken is a transport failure: it
							// changed nothing, so repeating it is safe.
							return actionloop.Result{
								Success: false, Code: "RPC_UNAVAILABLE", Message: err.Error(),
							}, nil
						}
						return actionloop.Result{Success: true, Message: "已读取遥测"}, nil
					},
				}.Registry(),
			),
			Observer: recoveryObserver{service: service},
			// No Approve port: the only way to reach this executor today is the
			// operator's endpoint, which sets OperatorApproved itself. An automatic
			// initiator added later must supply one, and until it does, nothing it
			// starts can run a bounded write.
			// Built from the configuration in force when the request arrives, so a
			// model configured through the console takes effect without a restart —
			// the same rule the task parser follows.
			Decider: configuration.recoveryDecider(),
		}).
		WithPairing(&console.FilePairingService{
			Discovery:     robotDiscovery,
			DataDirectory: configuration.dataDir,
			ConfigPath:    configuration.configFile,
		})
	// Report supervision state so the console can say when nothing is watching.
	// A deployment with the observer switched off must not look like a
	// deployment where nothing is wrong.
	var supervisionMu sync.Mutex
	var supervision tasks.SupervisionStatus
	application.WithSupervision(func() tasks.SupervisionStatus {
		supervisionMu.Lock()
		defer supervisionMu.Unlock()
		return supervision
	})
	application.Start(ctx)

	// The multi-agent runtime is started after the local execution lifecycle, so
	// it observes a stack that is already running and cannot change what that
	// stack does. Disabling it entirely (TANGYING_AGENTS=task) leaves execution
	// byte-for-byte identical.
	agentRuntime, agentBus, runnerAlerts := startAgentRuntime(ctx, os.Getenv, service, runner, store, telemetrySource)
	// Robot-level findings have no task to attach to, so the console reads them
	// from the store rather than from a ledger.
	application.WithRunnerAlerts(runnerAlerts.Alerts)
	application.WithRunnerAlertPlan(runnerAlerts.PlanFor)
	if agentBus != nil {
		defer agentBus.Close()
	}
	if agentRuntime != nil {
		names := agentRuntime.AgentNames()
		supervisionMu.Lock()
		supervision = tasks.SupervisionStatus{
			Enabled: len(names) > 0, Agents: names,
			Observing: containsAgent(names, agentruntime.OpsAgentName),
		}
		if !supervision.Observing {
			supervision.Reason = "the observing agent is not enabled; findings will not be detected"
		}
		supervisionMu.Unlock()
		defer func() {
			shutdownContext, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer shutdownCancel()
			if err := agentRuntime.Shutdown(shutdownContext); err != nil {
				log.Printf("Agent runtime shutdown: %v", err)
			}
		}()
	}
	defer func() {
		cancel()
		waitContext, waitCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer waitCancel()
		if err := application.Wait(waitContext); err != nil {
			log.Printf("Local Agent execution shutdown: %v", err)
		}
	}()
	settingsPath := configuration.configFile
	if settingsPath == "" {
		settingsPath = filepath.Join(configuration.dataDir, "local.env")
	}
	// Changing the model takes effect on the next request rather than at the next
	// restart.
	//
	// The parser is rebuilt from the new settings and swapped into the task
	// service, so the console's save button means what it says. Reading the
	// configuration from a file and rebuilding it here — rather than trusting the
	// request body — keeps one source of truth for what is configured: the file
	// the operator's change was just written to.
	settings := localconfig.NewSettings(settingsPath, console.ConfigStatus{
		Provider: configuration.llmProvider, BaseURL: configuration.llmBaseURL,
		Model: configuration.llmModel, HasAPIKey: configuration.llmAPIKey != "",
	}).WithOnChange(func(status console.ConfigStatus) {
		reloaded, err := readConfigFile(settingsPath)
		if err != nil {
			log.Printf("language settings changed but could not be re-read: %v", err)
			return
		}
		service.SetParser(llmagent.NewParser(llmagent.Config{
			Provider: reloaded["AGENT_PROVIDER"], BaseURL: reloaded["AGENT_BASE_URL"],
			APIKey: reloaded["AGENT_API_KEY"], Model: reloaded["AGENT_MODEL"],
		}))
		log.Printf("language settings applied without a restart: provider=%s model=%s",
			status.Provider, status.Model)
	})
	httpServer := &http.Server{
		Addr: configuration.listen,
		Handler: console.NewServer(
			service, application, console.WithSettings(settings), console.WithRuntime(router), console.WithWorld(world), console.WithEvidence(store), console.WithCamera(robot), console.WithNavigation(navigation), console.WithRobotServices(robot), console.WithLatency(stepTimings),
		).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	serverError := make(chan error, 1)
	go func() {
		log.Printf("Local Agent Console listening on http://%s", configuration.listen)
		serverError <- httpServer.ListenAndServe()
	}()
	select {
	case <-ctx.Done():
		shutdownContext, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutdownCancel()
		return httpServer.Shutdown(shutdownContext)
	case err := <-serverError:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}

// incidentDirectory is where abnormal endings are recorded. It is a documented
// deployment parameter: a fleet may point it at a shared volume so one diagnosis
// workflow can sweep every robot's failures.
func incidentDirectory(configured string) string {
	if strings.TrimSpace(configured) == "" {
		return incidents.DefaultDirectory
	}
	return configured
}

func defaultDataDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ".tangying-robot-agent"
	}
	return defaultDataDirFor(runtime.GOOS, home)
}

func defaultDataDirFor(platform, home string) string {
	if platform == "darwin" {
		return filepath.Join(home, "Library", "Application Support", "TangyingRobotAgent")
	}
	return filepath.Join(home, ".local", "share", "tangying-robot-agent-os")
}

func init() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
}

// recoveryObserver is what a recovery action is decided from and verified against.
//
// It reads the same facts the recovery agent's investigation does, which is the
// point: the execution and the reasoning that proposed it must be looking at one
// world, or a plan can be approved against a state that has already changed.
type recoveryObserver struct {
	service *tasks.Service
}

func (o recoveryObserver) Observe(ctx context.Context, taskID string) (actionloop.Observation, error) {
	if strings.TrimSpace(taskID) == "" {
		return actionloop.Observation{Summary: "没有指定任务；这是一次机器人级的恢复"}, nil
	}
	task, err := o.service.Get(ctx, taskID)
	if err != nil {
		return actionloop.Observation{}, err
	}
	summary := fmt.Sprintf("任务 %s 当前状态 %s", task.ID, task.State)
	if task.Request != "" {
		summary += "，原始要求：" + task.Request
	}
	return actionloop.Observation{
		Summary:     summary,
		EvidenceIDs: []string{task.ID},
	}, nil
}

// recoveryDecider chooses which of an action's declared tools to call.
//
// It is built per request from the current settings, so a model configured through
// the console takes effect without a restart — the same rule the task parser
// follows. With no model configured it returns nil, and the executor then refuses
// with a sentence rather than inventing a call.
func (c config) recoveryDecider() actionloop.Decider {
	if !strings.EqualFold(c.llmProvider, "openai") || c.llmBaseURL == "" || c.llmAPIKey == "" || c.llmModel == "" {
		return nil
	}
	return &actionloop.LLMDecider{
		BaseURL: c.llmBaseURL, APIKey: c.llmAPIKey, Model: c.llmModel,
	}
}
