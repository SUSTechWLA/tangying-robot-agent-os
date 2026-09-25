package main

import (
	"bufio"
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"net/url"
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
	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
	robotruntime "github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	"github.com/SUSTechWLA/tangying-robot-agent-os/incidents"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/agentharness"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/autorecovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/controllease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/discovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localconfig"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
	"github.com/SUSTechWLA/tangying-robot-agent-os/latency"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type config struct {
	configFile         string
	listen             string
	allowRemoteConsole bool
	robotAddress       string
	dataDir            string
	devInsecure        bool
	robotCA            string
	robotCert          string
	robotKey           string
	robotServerName    string
	robotSafetyProfile string
	robotID            string
	checkConfig        bool
	llmProvider        string
	llmBaseURL         string
	llmAPIKey          string
	llmModel           string
	llmSamples         int
	models             map[string]modelroute.Endpoint
	harness            agentharness.Profile
	assist             modelroute.Assist
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
	flags.BoolVar(&result.allowRemoteConsole, "allow-remote-console",
		strings.EqualFold(values["LOCAL_ALLOW_REMOTE"], "1") || strings.EqualFold(values["LOCAL_ALLOW_REMOTE"], "true"),
		"allow binding the Console to a non-loopback address (it has no authentication; put an authenticating proxy in front)")
	flags.StringVar(&result.robotAddress, "robot", configValue(values, "ROBOT_ADDRESS", "127.0.0.1:50051"), "Robot Runtime gRPC address")
	flags.StringVar(&result.dataDir, "data-dir", defaultDataDir(), "Local Agent data directory")
	flags.BoolVar(&result.devInsecure, "dev-insecure", false, "allow plaintext Robot Runtime connection")
	flags.StringVar(&result.robotCA, "robot-ca", values["ROBOT_CA"], "Robot Runtime CA certificate")
	flags.StringVar(&result.robotCert, "robot-cert", values["ROBOT_CERT"], "Local Agent client certificate")
	flags.StringVar(&result.robotKey, "robot-key", values["ROBOT_KEY"], "Local Agent client private key")
	flags.StringVar(&result.robotServerName, "robot-server-name", values["ROBOT_SERVER_NAME"], "expected Robot Runtime TLS server name")
	flags.StringVar(&result.robotSafetyProfile, "robot-safety-profile", values["ROBOT_SAFETY_PROFILE"], "explicit runtime safety profile for a commissioned endpoint")
	flags.StringVar(&result.robotID, "robot-id", configValue(values, "LOCAL_ROBOT_ID", "robot-local"), "identity of the one robot controlled by this agent")
	flags.BoolVar(&result.checkConfig, "check-config", false, "validate deployment configuration without contacting or moving the robot")
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
	// Refused by default, not merely warned about. A warning on stdout is read
	// once, at startup, by whoever is deploying; the exposure lasts as long as
	// the process does.
	if !loopbackListen(result.listen) && !result.allowRemoteConsole {
		return config{}, remoteConsoleRefusal(result.listen)
	}
	if strings.TrimSpace(result.robotID) == "" {
		return config{}, errors.New("local robot ID is required")
	}
	values["AGENT_PROVIDER"] = result.llmProvider
	values["AGENT_BASE_URL"] = result.llmBaseURL
	values["AGENT_API_KEY"] = result.llmAPIKey
	values["AGENT_MODEL"] = result.llmModel
	result.models = map[string]modelroute.Endpoint{}
	for _, stage := range []string{modelroute.Intent, modelroute.Planning, modelroute.Recovery} {
		endpoint := modelroute.Resolve(values, stage)
		if err := endpoint.Validate(); err != nil {
			return config{}, fmt.Errorf("%s model: %w", strings.ToLower(stage), err)
		}
		result.models[stage] = endpoint
	}
	result.harness, err = agentharness.New(agentharness.Edge, result.models)
	if err != nil {
		return config{}, err
	}
	result.assist = modelroute.Assist{URL: values["AGENT_CLOUD_ASSIST_URL"], RobotID: result.robotID,
		DeviceToken: values["AGENT_CLOUD_ASSIST_DEVICE_TOKEN"], CAFile: values["AGENT_CLOUD_ASSIST_CA"]}
	if result.assist.URL != "" && result.robotID == "robot-local" {
		return config{}, errors.New("cloud model assist requires an explicit LOCAL_ROBOT_ID")
	}
	for _, endpoint := range result.models {
		if _, err := result.assist.ClientFor(endpoint); err != nil {
			return config{}, err
		}
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
		"ROBOT_SAFETY_PROFILE": true, "LOCAL_ROBOT_ID": true,
		"AGENT_PROVIDER": true, "AGENT_BASE_URL": true, "AGENT_API_KEY": true,
		"AGENT_MODEL": true, "AGENT_ORCHESTRATION_SAMPLES": true,
		"AGENT_CLOUD_ASSIST_URL": true, "AGENT_CLOUD_ASSIST_DEVICE_TOKEN": true, "AGENT_CLOUD_ASSIST_CA": true,
	}
	for _, stage := range []string{modelroute.Intent, modelroute.Planning, modelroute.Recovery} {
		for _, field := range []string{"PROVIDER", "BASE_URL", "API_KEY", "MODEL"} {
			allowed["AGENT_"+stage+"_"+field] = true
		}
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
	if configuration.checkConfig {
		if err := checkDeploymentConfig(configuration); err != nil {
			log.Fatal(err)
		}
		fmt.Printf("single-robot config ready: robot=%s intent=%s planning=%s recovery=%s\n",
			configuration.robotID, configuration.model(modelroute.Intent).Model,
			configuration.model(modelroute.Planning).Model, configuration.model(modelroute.Recovery).Model)
		return
	}
	if err := run(configuration); err != nil {
		log.Fatal(err)
	}
}

func checkDeploymentConfig(configuration config) error {
	if configuration.devInsecure || configuration.robotID == "robot-local" {
		return errors.New("deployment requires explicit robot identity and Runtime mTLS")
	}
	if configuration.robotServerName == "" {
		return errors.New("Runtime TLS server name is required")
	}
	robot, err := robotclient.New(robotclient.Config{
		Address: configuration.robotAddress, CAFile: configuration.robotCA,
		CertFile: configuration.robotCert, KeyFile: configuration.robotKey,
		ServerName: configuration.robotServerName,
	})
	if err != nil {
		return fmt.Errorf("Runtime mTLS configuration: %w", err)
	}
	_ = robot.Close()
	for _, stage := range []string{modelroute.Intent, modelroute.Planning, modelroute.Recovery} {
		endpoint := configuration.model(stage)
		if !strings.EqualFold(endpoint.Provider, "openai") {
			continue
		}
		parsed, err := url.Parse(endpoint.BaseURL)
		if err != nil {
			return err
		}
		if parsed.Scheme == "http" && parsed.Hostname() != "localhost" && parsed.Hostname() != "127.0.0.1" && parsed.Hostname() != "::1" {
			return fmt.Errorf("%s model uses unencrypted non-loopback HTTP", stage)
		}
		if _, err := configuration.assist.ClientFor(endpoint); err != nil {
			return fmt.Errorf("%s cloud model assist: %w", stage, err)
		}
	}
	return nil
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
	// A freshly launched Runtime may not be listening yet. Verify its identity
	// before acquiring authority, but tolerate only a bounded startup race.
	probe, probeCancel := context.WithTimeout(context.Background(), 25*time.Second)
	var runtimeInfo robotruntime.Snapshot
	for {
		runtimeInfo, err = robot.Info(probe)
		if err == nil || probe.Err() != nil {
			break
		}
		select {
		case <-probe.Done():
		case <-time.After(250 * time.Millisecond):
		}
	}
	probeCancel()
	if err != nil {
		return fmt.Errorf("verify local robot identity: %w", err)
	}
	if configuration.robotID != "robot-local" && runtimeInfo.RobotID != configuration.robotID {
		return fmt.Errorf("configured robot %q does not match Runtime robot %q", configuration.robotID, runtimeInfo.RobotID)
	}
	controlLock, err := controllease.Acquire(runtimeInfo.RobotID, configuration.robotAddress)
	if err != nil {
		return err
	}
	defer controlLock.Close()
	navigation, err := console.NewNavigationReader(os.Getenv("TANGYING_NAVIGATION_URL"), os.Getenv("TANGYING_NAVIGATION_TOKEN"))
	if err != nil {
		return err
	}

	parser, planner, err := configuration.taskModels()
	if err != nil {
		return err
	}
	service := tasks.NewService(store, parser, planner)
	worldID := "local-" + configuration.robotID
	if configuration.robotID == "robot-local" {
		worldID = "local-default"
	}
	world := worldhub.New(worldID, 2*time.Second, 512)
	worldPublisher := worker.New(worker.Config{
		RobotID: configuration.robotID, Adapter: "local-runtime", WorldID: worldID,
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
	router := robotruntime.NewRouter(configuration.robotID, robot)
	grounder := agent.NewGrounderRouter(configuration.robotID, robot)
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
	recoveryDecider, err := configuration.recoveryDecider()
	if err != nil {
		cancel()
		return err
	}
	var recoveryModelMu sync.RWMutex
	currentRecoveryDecider := recoveryDecider
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
	// Named rather than inline because Record is attached below, once the agent
	// runtime that carries the ledger sink exists.
	recoveryExecutor := &recoveryexec.Executor{
		Profile: &configuration.harness,
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
				return recoveryexec.EvidenceFromSnapshot(snapshot, "", time.Now().UTC())
			}),
			recoveryexec.LocalTools{
				// Reconciliation, the action the catalog proposes most often for an
				// unknown outcome. It reads this task's step records, which is what
				// "对账" means concretely: comparing what the robot was told to do
				// against what it recorded doing.
				//
				// The task comes from the execution context rather than from an
				// argument, so a decider cannot point this at another task's history.
				ReadHistory: func(ctx context.Context, _ map[string]any) (actionloop.Result, error) {
					taskID := recoveryexec.TaskIDFrom(ctx)
					if taskID == "" {
						// A robot-level finding has no execution history to read.
						// That is an answer, not a failure, and it is reported as
						// one so the step does not look like a broken tool.
						return actionloop.Result{
							Success: true,
							Message: "这次恢复没有关联任务，因此没有执行记录可以对账",
						}, nil
					}
					runs, err := store.ListStepRuns(ctx, taskID)
					if err != nil {
						// A store that cannot answer is not an answer: the read
						// changed nothing, so repeating it is safe, and it is
						// classified as transport rather than as a finding.
						return actionloop.Result{
							Success: false, Code: "RPC_UNAVAILABLE",
							Message: "读取执行记录失败：" + err.Error(),
						}, nil
					}
					return actionloop.Result{
						Success: true,
						Message: fmt.Sprintf("已读取 %d 条执行记录", len(runs)),
						Detail:  stepRunDetail(runs),
					}, nil
				},
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
		DeciderProvider: func() actionloop.Decider {
			recoveryModelMu.RLock()
			defer recoveryModelMu.RUnlock()
			return currentRecoveryDecider
		},
	}
	// The automatic pass: the recovery agent's plans are carried out by the
	// recovery executor, but only their read-only steps. It is assembled here
	// with the executor because it is the same capability seen from the other
	// side — the executor runs what it is asked, and this decides what may be
	// asked of it without a person.
	autoRecovery := &autorecovery.Supervisor{
		Executor: recoveryExecutor,
		Catalog:  agentruntime.DefaultRecoveryCatalog(),
	}
	application := localapp.New(service, runner, memory.NewQueue[string](64)).
		WithIncidents(incidents.New(incidentDirectory(os.Getenv("TANGYING_INCIDENT_DIR")))).
		WithDiscoveredRobots(func() ([]discovery.Robot, bool) { return robotDiscovery.Robots(), true }).
		WithRecoveryExecution(recoveryExecutor).
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
	agentRuntime, agentBus, runnerAlerts := startAgentRuntime(
		ctx, os.Getenv, service, runner, store, telemetrySource, autoRecovery)
	// Recovery executions are recorded into the task ledger, so a person reading
	// a task a week later learns the same facts as the one who watched the button
	// run. The console is not a record.
	//
	// Attached here because the ledger sink lives on the runtime, and the runtime
	// deliberately starts after the local execution stack. It is still attached
	// before the console listens (that happens further down), so no request can
	// reach the executor with this unset.
	recoveryExecutor.Record = recoveryExecutionRecorder(agentBus)
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
	}).WithOnChange(func(status console.ConfigStatus) error {
		reloaded, err := readConfigFile(settingsPath)
		if err != nil {
			return err
		}
		updated := configuration
		updated.models = map[string]modelroute.Endpoint{}
		for _, stage := range []string{modelroute.Intent, modelroute.Planning, modelroute.Recovery} {
			updated.models[stage] = modelroute.Resolve(reloaded, stage)
			if err := updated.models[stage].Validate(); err != nil {
				return fmt.Errorf("%s model: %w", stage, err)
			}
		}
		updated.harness, err = agentharness.New(agentharness.Edge, updated.models)
		if err != nil {
			return err
		}
		newParser, newPlanner, err := updated.taskModels()
		if err != nil {
			return err
		}
		newRecoveryDecider, err := updated.recoveryDecider()
		if err != nil {
			return err
		}
		service.SetParser(newParser)
		service.SetPlanner(newPlanner)
		recoveryModelMu.Lock()
		currentRecoveryDecider = newRecoveryDecider
		recoveryModelMu.Unlock()
		log.Printf("language settings applied without a restart: provider=%s model=%s",
			status.Provider, status.Model)
		return nil
	})
	consoleServer := console.NewServer(
		service, application, console.WithSettings(settings), console.WithRuntime(router), console.WithWorld(world), console.WithEvidence(store), console.WithCamera(robot), console.WithNavigation(navigation), console.WithRobotServices(robot), console.WithLatency(stepTimings),
	)
	// Publish this process's console session for the local tools that need it —
	// the acceptance scripts, and the curl examples in the docs.
	//
	// The file is 0600 inside the data directory, which is the same boundary the
	// task database already has: a process that can read this can read every
	// task's text, so it grants nothing new. What it must not be is reachable over
	// the network — an endpoint serving it to whoever asks would make the token
	// mean "whoever asked", which is the reading it replaced.
	sessionPath := filepath.Join(configuration.dataDir, "console-session")
	// The address goes beside the token because a machine can run more than one
	// console, and a client that only knows "this directory" cannot tell which
	// token belongs to the console it is talking to. Recording the address is
	// what makes that answerable instead of guessed.
	addressPath := filepath.Join(configuration.dataDir, "console-address")
	if err := os.WriteFile(addressPath, []byte(configuration.listen+"\n"), 0o600); err != nil {
		log.Printf("console address file not written (%v)", err)
	}
	if err := os.WriteFile(sessionPath, []byte(consoleServer.SessionToken()+"\n"), 0o600); err != nil {
		// Not fatal: the console still works from a browser, which gets the token
		// as a cookie. Only the CLI path is lost, and it says so.
		log.Printf("console session file not written (%v); command-line clients will need the cookie", err)
	} else {
		log.Printf("Console session for command-line clients: %s", sessionPath)
	}
	httpServer := &http.Server{
		Addr:              configuration.listen,
		Handler:           consoleServer.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	serverError := make(chan error, 1)
	go func() {
		log.Printf("Local Agent Console listening on http://%s", configuration.listen)
		if !loopbackListen(configuration.listen) {
			// The operator opted in during configuration. Repeat the exposure
			// here as well: the decision was made once, possibly long ago, and
			// startup is the last moment anyone is looking.
			log.Print(remoteConsoleWarning(configuration.listen))
		}
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

// stepRunDetail renders the execution history the reconciliation read returned.
//
// It is deliberately a small, flat summary rather than the raw records: this
// travels into a task's replay, and a reader there needs to see which step reached
// which state, not every column the store happens to keep.
func stepRunDetail(runs []middleware.StepRun) map[string]any {
	if len(runs) == 0 {
		return nil
	}
	steps := make([]any, 0, len(runs))
	for _, run := range runs {
		entry := map[string]any{"stepId": run.StepID, "status": string(run.Status)}
		// The capability and safety class are what make a record actionable: "the
		// step that started and never finished was a physical one" is the fact an
		// operator needs, and it is not derivable from the status alone.
		if run.Capability != "" {
			entry["capability"] = run.Capability
		}
		if run.SafetyLevel != "" {
			entry["safetyLevel"] = run.SafetyLevel
		}
		steps = append(steps, entry)
	}
	return map[string]any{"stepCount": len(steps), "steps": steps}
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
	contextDocument := tasks.ContextFor(*task, "recovery", time.Now().UTC())
	summary := fmt.Sprintf("任务 %s 当前状态 %s", task.ID, task.State)
	if task.Request != "" {
		summary += "，原始要求：" + task.Request
	}
	return actionloop.Observation{
		Summary:     summary,
		Context:     &contextDocument,
		EvidenceIDs: []string{task.ID},
	}, nil
}

// recoveryExecutionRecorder writes one recovery attempt into the task ledger.
//
// It publishes on the bus, which is what appends to the per-task ledger, so
// recovery takes the same route into the record as every other agent's events
// rather than a second one that could drift.
//
// Three things about it are deliberate:
//
//   - A robot-level attempt has no task, and the ledger is per task. Such an
//     event is not filed under a task it does not describe — the projection
//     already drops an empty task id, which is the same rule agent.registered
//     follows.
//   - The publishing agent is "recovery", not "ops". Proposing a plan and running
//     it are different roles, and a replay that merged them would let "the system
//     suggested this" and "the system did this" read as one sentence.
//   - The event id is left for the runtime to assign, because it must be unique
//     per attempt. The runtime suppresses a repeat of an id it has already
//     delivered, and two executions of the same action on the same task are two
//     facts, not one fact arriving twice.
func recoveryExecutionRecorder(bus *agentruntime.AgentRuntime) func(
	context.Context, recoveryexec.Request, recoveryexec.Result, error,
) {
	return func(ctx context.Context, request recoveryexec.Request, result recoveryexec.Result, err error) {
		if bus == nil {
			return
		}
		trail := make([]string, 0, len(result.Trail))
		for _, step := range result.Trail {
			trail = append(trail, step.Name)
		}
		payload := agentcontract.RecoveryExecutedPayload{
			PlanID: request.PlanID, ActionID: result.ActionID,
			Executed: result.Executed, Verified: result.Verified,
			Summary:      recoveryExecutionSummary(result),
			Verification: result.Verification, Tools: request.Action.Tools,
			Trail: trail, OperatorApproved: request.OperatorApproved,
			ApprovalEvidence: request.ApprovalEvidence,
			OccurredAt:       time.Now().UTC(),
		}
		if err != nil {
			payload.Failed = err.Error()
		}
		encoded := payload.Encode()
		if len(result.Rounds) > 0 {
			encoded["decision_rounds"] = result.Rounds
		}
		bus.Publish(ctx, agentcontract.Event{
			TaskID: request.TaskID, Topic: agentcontract.TopicOpsRecoveryExecuted,
			Agent: "recovery", OccurredAt: payload.OccurredAt,
			// High, not normal: an execution that ran without a confirmed result is
			// exactly what an observer must not have to go looking for.
			Priority: agentcontract.PriorityHigh,
			Payload:  encoded,
		})
	}
}

// recoveryExecutionSummary states the outcome in one sentence, keeping the
// distinction that matters intact: ran-and-confirmed, ran-and-unknown, or did
// not run.
func recoveryExecutionSummary(result recoveryexec.Result) string {
	switch {
	case result.Executed && result.Verified:
		return fmt.Sprintf("恢复动作 %s 已执行并复验通过", result.ActionID)
	case result.Executed:
		return fmt.Sprintf("恢复动作 %s 已执行，但结果未确认：%s", result.ActionID, result.Reason)
	default:
		return fmt.Sprintf("恢复动作 %s 没有执行：%s", result.ActionID, result.Reason)
	}
}

func (c config) model(stage string) modelroute.Endpoint {
	if endpoint, ok := c.harness.Model(stage); ok {
		return endpoint
	}
	if endpoint, ok := c.models[stage]; ok {
		return endpoint
	}
	return modelroute.Resolve(map[string]string{
		"AGENT_PROVIDER": c.llmProvider, "AGENT_BASE_URL": c.llmBaseURL,
		"AGENT_API_KEY": c.llmAPIKey, "AGENT_MODEL": c.llmModel,
	}, stage)
}

func (c config) taskModels() (intent.Parser, orchestration.Planner, error) {
	understanding := c.model(modelroute.Intent)
	planning := c.model(modelroute.Planning)
	understandingClient, err := c.assist.ClientFor(understanding)
	if err != nil {
		return nil, nil, err
	}
	planningClient, err := c.assist.ClientFor(planning)
	if err != nil {
		return nil, nil, err
	}
	// The Fleet tool authenticates with the robot credential. Never forward a
	// locally inherited model API key as a second Authorization header.
	if understandingClient != nil {
		understanding.APIKey = ""
	}
	if planningClient != nil {
		planning.APIKey = ""
	}
	parser := llmagent.NewParser(llmagent.Config{
		Provider: understanding.Provider, BaseURL: understanding.BaseURL,
		APIKey: understanding.APIKey, Model: understanding.Model, HTTPClient: understandingClient,
	})
	planner := orchestration.New(manipulation.Catalog(), orchestration.Config{
		Provider: planning.Provider, BaseURL: planning.BaseURL,
		APIKey: planning.APIKey, Model: planning.Model, Samples: c.llmSamples, HTTPClient: planningClient,
	})
	return parser, planner, nil
}

// A recovery decision uses its own endpoint; the existing executor still
// restricts the offered tool catalog and requires approval for physical work.
func (c config) recoveryDecider() (actionloop.Decider, error) {
	endpoint := c.model(modelroute.Recovery)
	if !strings.EqualFold(endpoint.Provider, "openai") {
		return nil, nil
	}
	client, err := c.assist.ClientFor(endpoint)
	if err != nil {
		return nil, err
	}
	if client != nil {
		endpoint.APIKey = ""
	}
	return &actionloop.LLMDecider{BaseURL: endpoint.BaseURL, APIKey: endpoint.APIKey,
		Model: endpoint.Model, Client: client}, nil
}
