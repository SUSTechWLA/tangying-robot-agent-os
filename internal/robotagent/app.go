package robotagent

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

var validRoles = map[string]bool{"sim": true, "cloud": true, "local": true, "robot-pi": true}

type Receipt struct {
	Role      string `json:"role"`
	Version   string `json:"version"`
	Commit    string `json:"commit"`
	OS        string `json:"os"`
	Distro    string `json:"distro"`
	OSVersion string `json:"osVersion"`
	Arch      string `json:"arch"`
}

type Runner interface {
	Run(context.Context, string, ...string) error
}

type ExecRunner struct {
	Stdout io.Writer
	Stderr io.Writer
}

func (r ExecRunner) Run(ctx context.Context, name string, arguments ...string) error {
	command := exec.CommandContext(ctx, name, arguments...)
	command.Stdout = r.Stdout
	command.Stderr = r.Stderr
	command.Stdin = os.Stdin
	return command.Run()
}

type App struct {
	Version   string
	RootDir   string
	StateDir  string
	ConfigDir string
	Platform  string
	Runner    Runner
	Stdout    io.Writer
	Stderr    io.Writer
}

func (a *App) Run(ctx context.Context, arguments []string) error {
	if len(arguments) == 0 || arguments[0] == "help" || arguments[0] == "--help" || arguments[0] == "-h" {
		a.printHelp()
		return nil
	}
	command := arguments[0]
	rest := arguments[1:]
	switch command {
	case "version":
		return a.printVersion()
	case "start", "stop", "restart", "status", "logs":
		return a.lifecycle(ctx, command, rest)
	case "demo":
		if len(rest) != 0 {
			return errors.New("demo does not accept positional arguments")
		}
		return a.Runner.Run(ctx, "bash", filepath.Join(a.RootDir, "scripts", "demo.sh"))
	case "pair":
		return a.pair(ctx, rest)
	case "configure":
		return a.configure(rest)
	case "doctor":
		return a.doctor(ctx, rest)
	case "production-check":
		return a.productionCheck(ctx, rest)
	default:
		return fmt.Errorf("unknown command %q", command)
	}
}

func (a *App) printHelp() {
	fmt.Fprintln(a.Stdout, `Tangying Robot Agent OS

Usage:
  robot-agent doctor [ROLE]
  robot-agent production-check [robot-pi]
  robot-agent configure [ROLE] [KEY=VALUE ...]
  robot-agent pair ROBOT_HOST [--ssh-user USER] [--new-ca]
  robot-agent start [ROLE]
  robot-agent stop [ROLE]
  robot-agent restart [ROLE]
  robot-agent status [ROLE]
  robot-agent logs [ROLE] [--follow]
  robot-agent demo
  robot-agent version
  robot-agent help`)
}

func (a *App) printVersion() error {
	receipt, err := a.receipt()
	if err != nil {
		return err
	}
	fmt.Fprintf(a.Stdout, "cli=%s installed=%s commit=%s role=%s\n", a.Version, receipt.Version, receipt.Commit, receipt.Role)
	return nil
}

func (a *App) receipt() (Receipt, error) {
	content, err := os.ReadFile(filepath.Join(a.StateDir, "install.json"))
	if err != nil {
		return Receipt{}, fmt.Errorf("read installation receipt: %w", err)
	}
	var receipt Receipt
	if err := json.Unmarshal(content, &receipt); err != nil {
		return Receipt{}, fmt.Errorf("parse installation receipt: %w", err)
	}
	if !validRoles[receipt.Role] {
		return Receipt{}, fmt.Errorf("installation receipt has unknown role %q", receipt.Role)
	}
	return receipt, nil
}

func (a *App) lifecycle(ctx context.Context, operation string, arguments []string) error {
	role, follow, err := a.roleAndFollow(arguments)
	if err != nil {
		return err
	}
	name, commandArguments, err := a.lifecycleCommand(operation, role, follow)
	if err != nil {
		return err
	}
	return a.Runner.Run(ctx, name, commandArguments...)
}

func (a *App) roleAndFollow(arguments []string) (string, bool, error) {
	role := ""
	follow := false
	for _, argument := range arguments {
		if argument == "--follow" {
			follow = true
			continue
		}
		if strings.HasPrefix(argument, "-") {
			return "", false, fmt.Errorf("unknown lifecycle option %q", argument)
		}
		if role != "" {
			return "", false, errors.New("only one role may be specified")
		}
		role = argument
	}
	if role == "" {
		receipt, err := a.receipt()
		if err != nil {
			return "", false, err
		}
		role = receipt.Role
	}
	if !validRoles[role] {
		return "", false, fmt.Errorf("unknown role %q", role)
	}
	return role, follow, nil
}

func (a *App) lifecycleCommand(operation, role string, follow bool) (string, []string, error) {
	if follow && operation != "logs" {
		return "", nil, errors.New("--follow is valid only for logs")
	}
	switch role {
	case "cloud":
		base := []string{"compose", "--env-file", filepath.Join(a.ConfigDir, "cloud.env"), "-f", filepath.Join(a.RootDir, "deploy", "docker-compose.yml")}
		suffix := map[string][]string{
			"start":   {"up", "-d"},
			"stop":    {"stop"},
			"restart": {"restart"},
			"status":  {"ps"},
			"logs":    {"logs"},
		}[operation]
		if follow {
			suffix = append(suffix, "--follow")
		}
		return "docker", append(base, suffix...), nil
	case "local":
		if a.Platform == "darwin" {
			return a.localDarwinCommand(operation, follow)
		}
		return serviceCommand(operation, follow, true, "tangying-robot-local-agent.service")
	case "robot-pi":
		return serviceCommand(operation, follow, false, "tangying-robot-edge.service")
	case "sim":
		if operation == "start" || operation == "restart" {
			return "bash", []string{filepath.Join(a.RootDir, "scripts", "demo.sh")}, nil
		}
		if operation == "status" {
			return "bash", []string{filepath.Join(a.RootDir, "scripts", "demo.sh"), "--check"}, nil
		}
		return "pkill", []string{"-f", "tangying-(sim|local|cloud)"}, nil
	default:
		return "", nil, fmt.Errorf("unknown role %q", role)
	}
}

func serviceCommand(operation string, follow, user bool, service string) (string, []string, error) {
	if operation == "logs" {
		arguments := []string{"-u", service}
		if follow {
			arguments = append(arguments, "-f")
		}
		return "journalctl", arguments, nil
	}
	action := map[string]string{"start": "start", "stop": "stop", "restart": "restart", "status": "status"}[operation]
	arguments := []string{}
	if user {
		arguments = append(arguments, "--user")
	}
	arguments = append(arguments, action, service)
	return "systemctl", arguments, nil
}

func (a *App) localDarwinCommand(operation string, follow bool) (string, []string, error) {
	plist := filepath.Join(os.Getenv("HOME"), "Library", "LaunchAgents", "com.tangying.robot-agent.plist")
	domain := fmt.Sprintf("gui/%d", os.Getuid())
	switch operation {
	case "start":
		return "launchctl", []string{"bootstrap", domain, plist}, nil
	case "stop":
		return "launchctl", []string{"bootout", domain + "/com.tangying.robot-agent"}, nil
	case "restart":
		return "launchctl", []string{"kickstart", "-k", domain + "/com.tangying.robot-agent"}, nil
	case "status":
		return "launchctl", []string{"print", domain + "/com.tangying.robot-agent"}, nil
	case "logs":
		arguments := []string{"-n", "100"}
		if follow {
			arguments = append(arguments, "-f")
		}
		arguments = append(arguments, filepath.Join(a.StateDir, "logs", "local-agent.log"))
		return "tail", arguments, nil
	default:
		return "", nil, fmt.Errorf("unsupported operation %q", operation)
	}
}

func (a *App) pair(ctx context.Context, arguments []string) error {
	if len(arguments) == 0 || strings.HasPrefix(arguments[0], "-") {
		return errors.New("pair requires ROBOT_HOST")
	}
	host := arguments[0]
	sshUser := "ubuntu"
	newCA := false
	for index := 1; index < len(arguments); index++ {
		switch arguments[index] {
		case "--ssh-user":
			if index+1 >= len(arguments) {
				return errors.New("--ssh-user requires a value")
			}
			sshUser = arguments[index+1]
			index++
		case "--new-ca":
			newCA = true
		default:
			return fmt.Errorf("unknown pair option %q", arguments[index])
		}
	}
	scriptArguments := []string{filepath.Join(a.RootDir, "scripts", "pair-robot.sh"), host, "--ssh-user", sshUser}
	if newCA {
		scriptArguments = append(scriptArguments, "--new-ca")
	}
	return a.Runner.Run(ctx, "bash", scriptArguments...)
}

var allowedConfigKeys = map[string]map[string]bool{
	"cloud":    {"CLOUD_BIND": true, "CLOUD_PORT": true, "POSTGRES_BIND": true, "POSTGRES_PORT": true, "POSTGRES_DB": true, "POSTGRES_USER": true, "POSTGRES_PASSWORD": true, "AGENT_PROVIDER": true, "AGENT_BASE_URL": true, "AGENT_API_KEY": true, "AGENT_MODEL": true, "AGENT_ORCHESTRATION_SAMPLES": true},
	"local":    {"CLOUD_URL": true, "ROBOT_ADDRESS": true, "ROBOT_SERVER_NAME": true, "AGENT_ID": true, "ROBOT_CA": true, "ROBOT_CERT": true, "ROBOT_KEY": true},
	"robot-pi": {"ROBOT_GRPC_LISTEN": true, "ROBOT_SERVER_KEY": true, "ROBOT_SERVER_CERT": true, "ROBOT_CLIENT_CA": true, "XLEROBOT_PORT1": true, "XLEROBOT_PORT2": true, "XLEROBOT_CALIBRATION": true, "XLEROBOT_CALIBRATION_ROOT": true, "XLEROBOT_UPSTREAM_ROOT": true, "XLEROBOT_MAX_RELATIVE_TARGET": true, "XLEROBOT_MAX_ACTION_CHUNK_LENGTH": true, "ROBOT_ENTITY_PROVIDER": true, "ROBOT_POLICY_PROVIDER": true, "ROBOT_VERIFIER_PROVIDER": true},
	"sim":      {},
}

func (a *App) configure(arguments []string) error {
	role, values, err := a.configRoleAndValues(arguments)
	if err != nil {
		return err
	}
	if len(values) == 0 {
		fmt.Fprintf(a.Stdout, "config=%s\n", filepath.Join(a.ConfigDir, role+".env"))
		return nil
	}
	destination := filepath.Join(a.ConfigDir, role+".env")
	current, err := readEnvironment(destination)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	for key, value := range values {
		current[key] = value
	}
	return writeEnvironment(destination, current)
}

func (a *App) configRoleAndValues(arguments []string) (string, map[string]string, error) {
	role := ""
	if len(arguments) > 0 && validRoles[arguments[0]] {
		role = arguments[0]
		arguments = arguments[1:]
	}
	if role == "" {
		receipt, err := a.receipt()
		if err != nil {
			return "", nil, err
		}
		role = receipt.Role
	}
	values := map[string]string{}
	for _, assignment := range arguments {
		key, value, ok := strings.Cut(assignment, "=")
		if !ok || !allowedConfigKeys[role][key] {
			return "", nil, fmt.Errorf("configuration key is not allowed for %s: %q", role, assignment)
		}
		if strings.ContainsAny(value, "\r\n\x00") {
			return "", nil, fmt.Errorf("configuration value for %s contains a forbidden character", key)
		}
		values[key] = value
	}
	return role, values, nil
}

func readEnvironment(path string) (map[string]string, error) {
	file, err := os.Open(path)
	if err != nil {
		return map[string]string{}, err
	}
	defer file.Close()
	values := map[string]string{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if ok {
			values[key] = value
		}
	}
	return values, scanner.Err()
}

func writeEnvironment(path string, values map[string]string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var content strings.Builder
	for _, key := range keys {
		fmt.Fprintf(&content, "%s=%s\n", key, values[key])
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, []byte(content.String()), 0o600); err != nil {
		return err
	}
	if err := os.Chmod(temporary, 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

func (a *App) doctor(ctx context.Context, arguments []string) error {
	if len(arguments) > 1 || (len(arguments) == 1 && !validRoles[arguments[0]]) {
		option := strings.Join(arguments, " ")
		return fmt.Errorf("unknown doctor option or role %q", option)
	}
	receipt, err := a.receipt()
	if err != nil {
		return err
	}
	role := receipt.Role
	if len(arguments) == 1 {
		role = arguments[0]
	}
	fmt.Fprintf(a.Stdout, "PASS receipt role=%s version=%s platform=%s/%s\n", role, receipt.Version, receipt.OS, receipt.Arch)
	if role != "sim" {
		path := filepath.Join(a.ConfigDir, role+".env")
		info, statErr := os.Stat(path)
		if statErr != nil {
			return fmt.Errorf("FAIL config %s: %w", path, statErr)
		}
		if info.Mode().Perm()&0o077 != 0 {
			return fmt.Errorf("FAIL config permissions %s: %o", path, info.Mode().Perm())
		}
		fmt.Fprintf(a.Stdout, "PASS config=%s permissions=%o\n", path, info.Mode().Perm())
	}
	if role == "robot-pi" {
		return a.Runner.Run(
			ctx,
			"bash",
			filepath.Join(a.RootDir, "scripts", "robot-pi-preflight.sh"),
			filepath.Join(a.ConfigDir, "robot-pi.env"),
		)
	}
	return nil
}

// productionCheck is the explicit go/no-go gate for physical XLeRobot tasks.
// It runs the no-motion preflight and then requires configured perception,
// policy and verifier providers plus recorded hardware-trial evidence.
func (a *App) productionCheck(ctx context.Context, arguments []string) error {
	if len(arguments) > 1 {
		return fmt.Errorf("production-check accepts at most one role")
	}
	receipt, err := a.receipt()
	if err != nil {
		return err
	}
	role := receipt.Role
	if len(arguments) == 1 {
		role = arguments[0]
	}
	if role != "robot-pi" {
		return fmt.Errorf("production check is only defined for robot-pi role")
	}
	return a.Runner.Run(
		ctx,
		filepath.Join(a.RootDir, ".venv", "bin", "python"),
		filepath.Join(a.RootDir, "scripts", "xlerobot_production_check.py"),
		filepath.Join(a.ConfigDir, "robot-pi.env"),
	)
}

func DefaultApp(version string, stdout, stderr io.Writer) *App {
	home, _ := os.UserHomeDir()
	executable, _ := os.Executable()
	root := resolveRoot(os.Getenv("ROBOT_AGENT_ROOT"), executable, runtime.GOOS)
	state, config := resolveDirectories(
		runtime.GOOS,
		home,
		os.Getenv("ROBOT_AGENT_STATE_DIR"),
		os.Getenv("ROBOT_AGENT_CONFIG_DIR"),
	)
	return &App{
		Version: version, RootDir: root, StateDir: state, ConfigDir: config,
		Platform: runtime.GOOS, Runner: ExecRunner{Stdout: stdout, Stderr: stderr},
		Stdout: stdout, Stderr: stderr,
	}
}

func resolveRoot(explicit, executable, platform string) string {
	if explicit != "" {
		return explicit
	}
	if executable != "" {
		if resolved, err := filepath.EvalSymlinks(executable); err == nil {
			executable = resolved
		}
		candidate := filepath.Dir(filepath.Dir(executable))
		if _, err := os.Stat(filepath.Join(candidate, "install.sh")); err == nil {
			return candidate
		}
	}
	if platform == "darwin" {
		return "/Users/Shared/TangyingRobotAgent"
	}
	return "/opt/tangying-robot-agent-os"
}

func resolveDirectories(platform, home, explicitState, explicitConfig string) (string, string) {
	state := explicitState
	config := explicitConfig
	if platform == "darwin" {
		if state == "" {
			state = filepath.Join(home, "Library", "Application Support", "TangyingRobotAgent")
		}
		if config == "" {
			config = state
		}
		return state, config
	}

	userState := filepath.Join(home, ".local", "share", "tangying-robot-agent-os")
	if state == "" {
		if _, err := os.Stat(filepath.Join(userState, "install.json")); err == nil {
			state = userState
		} else {
			state = "/var/lib/tangying-robot-agent-os"
		}
	}
	if config == "" {
		if state == userState {
			config = filepath.Join(home, ".config", "tangying-robot-agent-os")
		} else {
			config = "/etc/tangying-robot-agent-os"
		}
	}
	return state, config
}
