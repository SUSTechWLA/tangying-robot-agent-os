package console

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/discovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/pairing"
)

// Pairing a robot from the console, with no SSH anywhere.
//
// This is the step that turns "the robot appeared in a list" into "the robot
// works". The SSH script did the same job and did it well, but it needed a
// hostname typed by hand, an SSH account, key-based authentication and `sudo` on
// the far side — a technician's workflow, and the last thing standing between an
// owner and a usable robot.
//
// # What the operator supplies
//
// One code, which the robot prints when it starts unpaired. It is single-use,
// expires with the pairing window, and after a handful of wrong guesses the window
// shuts and has to be reopened on the robot. Someone who has the code can pair,
// and that is the intended meaning of the code: it is what says "I am standing at
// this robot". Physical possession is the root of trust, which is why the code is
// never broadcast.
//
// # What it does not do
//
// It does not connect to a robot that was not found announcing itself, and it does
// not trust the announcement for anything but an address. The address the
// operator chose is the address that gets dialled and the name that goes into the
// certificate, because the announcement is unauthenticated and a certificate
// built from an attacker's input would be built from an attacker's input.

// pairingAvailability is what lets the console say "this deployment cannot pair"
// rather than "your pairing failed". The two send an operator to different places.
type pairingAvailability interface {
	PairingAvailable() bool
}

// PairingService performs the pairing and records the agent's own side of it.
type PairingService interface {
	// Pair joins one robot and installs the agent's own credentials.
	Pair(ctx context.Context, request PairRequest) (PairReport, error)
}

// PairRequest is one pairing attempt.
type PairRequest struct {
	// RobotID is the identity the agent believes it is pairing with.
	RobotID string `json:"robotId"`
	// Address is host:port of the robot's runtime, used for the certificate's
	// names and written into the agent's configuration.
	Address string `json:"address"`
	// Code is the one-time code the robot printed.
	Code string `json:"code"`
	// EnrollmentPort overrides the robot's pairing port.
	EnrollmentPort int `json:"enrollmentPort,omitempty"`
}

// PairReport says what was done and what is left.
type PairReport struct {
	RobotID string `json:"robotId"`
	Address string `json:"address"`
	// Paired is true only when the robot acknowledged installing the material.
	Paired bool `json:"paired"`
	// RestartRequired is true when the agent's own credentials changed.
	//
	// The robot client is built at startup with the certificates that existed
	// then, so a pairing cannot take effect in a running agent. Reporting it is
	// the difference between "paired" and "looks paired".
	RestartRequired bool `json:"restartRequired"`
	// Detail is a sentence for the operator.
	Detail string `json:"detail"`
}

// pairingRequest mirrors the JSON body.
type pairingRequestBody struct {
	RobotID        string `json:"robotId"`
	Address        string `json:"address"`
	Code           string `json:"code"`
	EnrollmentPort int    `json:"enrollmentPort"`
}

func (s *Server) pairRobot(w http.ResponseWriter, r *http.Request) {
	if !s.allowOperatorWrite(w, r) {
		return
	}
	var body pairingRequestBody
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_PAIRING_REQUEST", "请求格式不正确")
		return
	}
	if strings.TrimSpace(body.Code) == "" {
		// Named rather than generic: the code is the one thing the operator has to
		// go and find, and a message that does not say so sends them looking
		// through the rest of the page.
		writeError(w, http.StatusBadRequest, "PAIRING_CODE_REQUIRED",
			"需要机器人启动时打印的配对码；没有码就无法确认对面是这台机器人。")
		return
	}
	if strings.TrimSpace(body.RobotID) == "" || strings.TrimSpace(body.Address) == "" {
		writeError(w, http.StatusBadRequest, "PAIRING_TARGET_REQUIRED",
			"请先选择要配对的机器人。")
		return
	}
	service, ok := s.executor.(PairingService)
	if !ok || service == nil {
		writeError(w, http.StatusServiceUnavailable, "PAIRING_UNAVAILABLE",
			"这台 Local Agent 没有配置配对能力。")
		return
	}
	// "Cannot pair" and "pairing failed" are different answers. A deployment with
	// no pairing service is not a failed attempt, and reporting it as one would
	// send an operator to check a robot that was never contacted.
	if availability, ok := s.executor.(pairingAvailability); ok && !availability.PairingAvailable() {
		writeError(w, http.StatusServiceUnavailable, "PAIRING_UNAVAILABLE",
			"这台 Local Agent 没有配置配对能力。")
		return
	}
	// Pairing is a local network act with a human waiting on it. The bound is
	// generous because a certificate is being signed and written on the far side,
	// and a timeout that fires mid-write would leave the robot half-paired.
	ctx, cancel := context.WithTimeout(r.Context(), 45*time.Second)
	defer cancel()

	report, err := service.Pair(ctx, PairRequest{
		RobotID: strings.TrimSpace(body.RobotID), Address: strings.TrimSpace(body.Address),
		Code: body.Code, EnrollmentPort: body.EnrollmentPort,
	})
	if err != nil {
		status := http.StatusBadGateway
		code := "PAIRING_FAILED"
		switch {
		case errors.Is(err, pairing.ErrWrongCode):
			code = "PAIRING_CODE_REJECTED"
			status = http.StatusUnauthorized
		case errors.Is(err, pairing.ErrUnreachable):
			code = "ROBOT_NOT_PAIRING"
			status = http.StatusConflict
		case errors.Is(err, pairing.ErrRobotRefused):
			code = "ROBOT_REFUSED_PAIRING"
			status = http.StatusConflict
		}
		writeError(w, status, code, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, report)
}

// FilePairingService is the real implementation: it signs, sends, and writes the
// agent's own half.
type FilePairingService struct {
	// Discovery finds the robot that is announcing itself, so the agent pairs with
	// something it has actually heard rather than with an address a form supplied.
	Discovery interface {
		Robots() []discovery.Robot
	}
	// DataDirectory holds the agent's CA, its client certificate and its config.
	DataDirectory string
	// ConfigPath is the agent's local.env.
	ConfigPath string
	// Port overrides the robot's enrollment port. Zero means the protocol default.
	Port int
	// Client, when set, replaces the network client. Tests use it.
	Client *pairing.Client
}

// Pair signs for the robot, installs the material, and writes the agent's half.
func (s *FilePairingService) Pair(ctx context.Context, request PairRequest) (PairReport, error) {
	host, _, err := net.SplitHostPort(request.Address)
	if err != nil {
		// An address without a port is accepted, because an operator reading a
		// list may type just the name.
		host = request.Address
	}
	host = strings.TrimSpace(host)
	if host == "" {
		return PairReport{}, errors.New("机器人地址为空")
	}
	// The robot must be one this agent has heard announcing itself.
	//
	// Not a security boundary — the code is that — but a correctness one: pairing
	// with an address nobody has heard from is pairing with a guess, and the
	// certificate would be issued for a name that may belong to something else.
	if s.Discovery != nil {
		found := false
		for _, robot := range s.Discovery.Robots() {
			if robot.RobotID == request.RobotID && (robot.Address == request.Address || robot.SourceIP == host || robot.Hostname == host) {
				found = true
				break
			}
		}
		if !found {
			return PairReport{}, fmt.Errorf(
				"没有发现叫 %s 的机器人正在广播；请确认它已开机、和这台电脑在同一网络，然后重新扫描",
				request.RobotID)
		}
	}
	// Where to send the request, in order of how much it can be trusted: what the
	// operator's request asked for, what the robot announced, then the protocol
	// default. The announcement is unauthenticated, but it is a hint about a port
	// rather than an identity, and the code is what decides whether the request is
	// accepted.
	enrollmentPort := request.EnrollmentPort
	if enrollmentPort <= 0 && s.Discovery != nil {
		for _, robot := range s.Discovery.Robots() {
			if robot.RobotID == request.RobotID && robot.EnrollmentPort > 0 {
				enrollmentPort = robot.EnrollmentPort
				break
			}
		}
	}
	ip := host
	if parsed := net.ParseIP(host); parsed == nil {
		// The robot announced its own address; use it for the certificate's IP
		// entry so the certificate names what the agent will actually dial.
		if s.Discovery != nil {
			for _, robot := range s.Discovery.Robots() {
				if robot.RobotID == request.RobotID && robot.SourceIP != "" {
					ip = robot.SourceIP
					break
				}
			}
		}
	}

	certificateDirectory := filepath.Join(s.DataDirectory, "certs")
	authority, err := pairing.LoadOrCreateAuthority(
		filepath.Join(certificateDirectory, "ca.crt"),
		filepath.Join(certificateDirectory, "ca.key"),
	)
	if err != nil {
		return PairReport{}, err
	}
	client := s.Client
	if client == nil {
		// The port comes from the announcement rather than from an assumption: a
		// deployment may run the pairing listener anywhere, and a wrong guess
		// reads as "the robot is not offering to be paired" while it waits.
		port := enrollmentPort
		if port <= 0 {
			port = s.Port
		}
		client = &pairing.Client{Port: port}
	}
	result, err := client.Pair(ctx, request.Code, pairing.Address{
		RobotID: request.RobotID, Host: host, IP: ip, Authority: authority,
	})
	if err != nil {
		return PairReport{}, err
	}

	// The agent's own half: a client certificate, and the configuration that
	// points at the robot with it. Written after the robot accepted, so a failed
	// pairing never leaves the agent pointing at a robot that will refuse it.
	if err := s.writeAgentHalf(certificateDirectory, authority, request.Address); err != nil {
		// The robot is paired; saying otherwise would be wrong. The error is
		// reported through Detail so the operator knows what is unfinished.
		return PairReport{
			RobotID: result.RobotID, Address: result.Address, Paired: true,
			Detail: "机器人已配对，但本机凭据没能写入：" + err.Error(),
		}, nil
	}
	return PairReport{
		RobotID: result.RobotID, Address: request.Address, Paired: true, RestartRequired: true,
		Detail: "配对完成。重启本机 Local Agent 后生效——机器人凭据是在启动时读取的。",
	}, nil
}

// WriteAgentHalfForTest writes the agent's own half of a pairing, for tests that
// need to check the file it produces without a robot to pair with.
func WriteAgentHalfForTest(dataDirectory, configPath, address string) error {
	certificateDirectory := filepath.Join(dataDirectory, "certs")
	authority, err := pairing.LoadOrCreateAuthority(
		filepath.Join(certificateDirectory, "ca.crt"), filepath.Join(certificateDirectory, "ca.key"))
	if err != nil {
		return err
	}
	service := &FilePairingService{DataDirectory: dataDirectory, ConfigPath: configPath}
	return service.writeAgentHalf(certificateDirectory, authority, address)
}

func (s *FilePairingService) writeAgentHalf(certificateDirectory string, authority *pairing.Authority, address string) error {
	certificatePEM, keyPEM, err := authority.AgentCertificate()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(certificateDirectory, 0o700); err != nil {
		return err
	}
	certificatePath := filepath.Join(certificateDirectory, "local-agent.crt")
	keyPath := filepath.Join(certificateDirectory, "local-agent.key")
	if err := writeSecret(certificatePath, certificatePEM, 0o644); err != nil {
		return err
	}
	if err := writeSecret(keyPath, keyPEM, 0o600); err != nil {
		return err
	}
	if strings.TrimSpace(s.ConfigPath) == "" {
		return nil
	}
	host, _, splitErr := net.SplitHostPort(address)
	if splitErr != nil {
		host = address
	}
	// The same five keys `scripts/pair-robot.sh` writes. A robot paired either way
	// must leave the agent configured identically, or the two paths become two
	// kinds of pairing.
	return updateEnvFile(s.ConfigPath, map[string]string{
		"ROBOT_ADDRESS":     address,
		"ROBOT_SERVER_NAME": host,
		"ROBOT_CA":          filepath.Join(certificateDirectory, "ca.crt"),
		"ROBOT_CERT":        certificatePath,
		"ROBOT_KEY":         keyPath,
	})
}

func writeSecret(path string, content []byte, mode os.FileMode) error {
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, content, mode); err != nil {
		return err
	}
	if err := os.Chmod(temporary, mode); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

// updateEnvFile rewrites the keys it is given and preserves everything else.
//
// Preserving the rest matters: the same file holds the model configuration, and a
// pairing that dropped the API key would look like a pairing that broke the
// agent's language ability.
//
// The first version of this rebuilt the file from a list of keys rather than from
// the lines it read, and silently wrote `AGENT_PROVIDER` with no value while
// re-appending the real line at the end. The file still *contained* the right
// string, so the test that checked for it passed. The test now compares the whole
// file, because "contains" is not "preserved".
func updateEnvFile(path string, values map[string]string) error {
	remaining := make(map[string]string, len(values))
	for key, value := range values {
		remaining[key] = value
	}
	var builder strings.Builder
	if data, err := os.ReadFile(filepath.Clean(path)); err == nil {
		text := strings.TrimRight(string(data), "\n")
		if text != "" {
			for _, line := range strings.Split(text, "\n") {
				trimmed := strings.TrimSpace(line)
				key, _, found := strings.Cut(trimmed, "=")
				if found && !strings.HasPrefix(trimmed, "#") {
					if replacement, ok := remaining[key]; ok {
						builder.WriteString(key + "=" + replacement + "\n")
						delete(remaining, key)
						continue
					}
				}
				builder.WriteString(line + "\n")
			}
		}
	}
	appended := make([]string, 0, len(remaining))
	for key := range remaining {
		appended = append(appended, key)
	}
	// Sorted so two runs of the same pairing produce the same file, which is what
	// makes a diff of a configuration meaningful.
	sort.Strings(appended)
	for _, key := range appended {
		builder.WriteString(key + "=" + remaining[key] + "\n")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return writeSecret(path, []byte(builder.String()), 0o600)
}
