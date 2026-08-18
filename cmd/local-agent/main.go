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
	"syscall"
	"time"

	llmagent "github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localconfig"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type config struct {
	configFile      string
	listen          string
	robotAddress    string
	dataDir         string
	devInsecure     bool
	robotCA         string
	robotCert       string
	robotKey        string
	robotServerName string
	llmProvider     string
	llmBaseURL      string
	llmAPIKey       string
	llmModel        string
	llmSamples      int
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
	return "", nil
}

func readConfigFile(path string) (map[string]string, error) {
	values := map[string]string{}
	if path == "" {
		return values, nil
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open Local Agent config: %w", err)
	}
	defer file.Close()
	allowed := map[string]bool{
		"LOCAL_LISTEN": true, "ROBOT_ADDRESS": true, "ROBOT_SERVER_NAME": true,
		"ROBOT_CA": true, "ROBOT_CERT": true, "ROBOT_KEY": true,
		"AGENT_PROVIDER": true, "AGENT_BASE_URL": true, "AGENT_API_KEY": true,
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
	store, err := localstore.Open(filepath.Join(configuration.dataDir, "agent.db"))
	if err != nil {
		return err
	}
	defer store.Close()
	robot, err := robotclient.New(robotclient.Config{
		Address: configuration.robotAddress, DevInsecure: configuration.devInsecure,
		CAFile: configuration.robotCA, CertFile: configuration.robotCert, KeyFile: configuration.robotKey,
		ServerName: configuration.robotServerName,
	})
	if err != nil {
		return err
	}
	defer robot.Close()

	parser := llmagent.NewParser(llmagent.Config{
		Provider: configuration.llmProvider, BaseURL: configuration.llmBaseURL,
		APIKey: configuration.llmAPIKey, Model: configuration.llmModel,
	})
	planner := orchestration.New(manipulation.Catalog(), orchestration.Config{
		Provider: configuration.llmProvider, BaseURL: configuration.llmBaseURL,
		APIKey: configuration.llmAPIKey, Model: configuration.llmModel, Samples: configuration.llmSamples,
	})
	service := tasks.NewService(store, parser, planner)
	runner := agent.NewRunner(store, robot)
	runner.Telemetry = func(ctx context.Context, snapshot telemetry.Snapshot) error {
		service.PublishTelemetry(ctx, snapshot)
		return nil
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	application := localapp.New(service, runner)
	application.Start(ctx)
	settingsPath := configuration.configFile
	if settingsPath == "" {
		settingsPath = filepath.Join(configuration.dataDir, "local.env")
	}
	settings := localconfig.NewSettings(settingsPath, console.ConfigStatus{
		Provider: configuration.llmProvider, BaseURL: configuration.llmBaseURL,
		Model: configuration.llmModel, HasAPIKey: configuration.llmAPIKey != "",
	})
	httpServer := &http.Server{
		Addr: configuration.listen,
		Handler: console.NewServer(
			service, application, console.WithSettings(settings), console.WithRuntime(robot),
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
