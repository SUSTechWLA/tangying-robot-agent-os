// Package localconfig persists the desktop Local Agent's operator-managed
// settings. Secrets are never returned through the Console status API.
package localconfig

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

type Settings struct {
	path   string
	mu     sync.RWMutex
	status console.ConfigStatus
	// onChange is called after a successful write, so the running process can
	// apply the new configuration without being restarted.
	//
	// A settings screen whose save button produces "restart to apply" is a
	// settings screen that is really a deployment form. The operator changed a
	// value; the value should change.
	onChange func(console.ConfigStatus) error
}

// WithOnChange installs a callback invoked after a successful update.
//
// It runs after the file is written and before the call returns, so a caller that
// sees success knows the running process has been told. A callback that panics or
// blocks is the caller's problem; this does not swallow either, because a silently
// ignored failure to apply is exactly the state this exists to prevent.
func (s *Settings) WithOnChange(onChange func(console.ConfigStatus) error) *Settings {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.onChange = onChange
	return s
}

func NewSettings(path string, initial console.ConfigStatus) *Settings {
	settings := &Settings{path: path, status: initial}
	if values, err := readValues(path); err == nil {
		if values["AGENT_PROVIDER"] != "" {
			settings.status.Provider = values["AGENT_PROVIDER"]
		}
		settings.status.BaseURL = values["AGENT_BASE_URL"]
		settings.status.Model = values["AGENT_MODEL"]
		settings.status.HasAPIKey = values["AGENT_API_KEY"] != ""
		settings.status.Stages = stageStatuses(values)
	}
	if settings.status.Provider == "" {
		settings.status.Provider = "deterministic"
	}
	if len(settings.status.Stages) == 0 {
		settings.status.Stages = stageStatuses(map[string]string{
			"AGENT_PROVIDER": settings.status.Provider,
			"AGENT_BASE_URL": settings.status.BaseURL,
			"AGENT_MODEL":    settings.status.Model,
		})
	}
	return settings
}

func (s *Settings) Status() console.ConfigStatus {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.status
}

func (s *Settings) UpdateLLM(input console.LLMConfig) error {
	provider := strings.TrimSpace(input.Provider)
	if provider != "deterministic" && provider != "openai" {
		return fmt.Errorf("unsupported provider %q", provider)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	values, err := readValues(s.path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if values == nil {
		values = map[string]string{}
	}
	previousContents, readErr := os.ReadFile(s.path)
	if readErr != nil && !errors.Is(readErr, os.ErrNotExist) {
		return readErr
	}
	previousStatus := s.status
	apiKey := strings.TrimSpace(input.APIKey)
	if input.ClearAPIKey && apiKey != "" {
		return errors.New("cannot set and clear the API key in one update")
	}
	if apiKey == "" && !input.ClearAPIKey && strings.TrimSpace(input.BaseURL) == strings.TrimSpace(values["AGENT_BASE_URL"]) {
		apiKey = values["AGENT_API_KEY"]
	}
	baseURL := strings.TrimSpace(input.BaseURL)
	model := strings.TrimSpace(input.Model)
	if provider == "openai" && (baseURL == "" || model == "") {
		return errors.New("openai provider requires baseUrl and model")
	}
	values["AGENT_PROVIDER"] = provider
	values["AGENT_BASE_URL"] = baseURL
	values["AGENT_MODEL"] = model
	values["AGENT_API_KEY"] = apiKey
	if err := writeValues(s.path, values); err != nil {
		return err
	}
	status := console.ConfigStatus{
		Provider: provider, BaseURL: baseURL, Model: model, HasAPIKey: apiKey != "",
		Stages: stageStatuses(values),
		// RestartRequired is the fallback answer. It is cleared only when a
		// callback actually applied the change, because claiming a setting took
		// effect when nothing was told is worse than asking for a restart.
		RestartRequired: true,
	}
	onChange := s.onChange
	s.status = status
	if onChange != nil {
		if err := onChange(status); err != nil {
			var restoreErr error
			if errors.Is(readErr, os.ErrNotExist) {
				restoreErr = os.Remove(s.path)
			} else {
				restoreErr = writeRawValues(s.path, previousContents)
			}
			if restoreErr != nil {
				return fmt.Errorf("model configuration could not be applied (%w) or restored (%v)", err, restoreErr)
			}
			s.status = previousStatus
			return fmt.Errorf("model configuration could not be applied; previous config restored: %w", err)
		}
		s.status.RestartRequired = false
	}
	return nil
}

func writeRawValues(path string, contents []byte) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), ".local.env-rollback-*")
	if err != nil {
		return err
	}
	defer os.Remove(temporary.Name())
	if err := temporary.Chmod(0600); err != nil {
		temporary.Close()
		return err
	}
	if _, err := temporary.Write(contents); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporary.Name(), path)
}

func stageStatuses(values map[string]string) map[string]console.ModelStageStatus {
	result := map[string]console.ModelStageStatus{}
	for _, stage := range []string{modelroute.Intent, modelroute.Planning, modelroute.Recovery} {
		endpoint := modelroute.Resolve(values, stage)
		result[strings.ToLower(stage)] = console.ModelStageStatus{
			Provider: endpoint.Provider, BaseURL: endpoint.BaseURL, Model: endpoint.Model,
			HasAPIKey: endpoint.APIKey != "", UsesCloudAssist: values["AGENT_CLOUD_ASSIST_URL"] != "" &&
				strings.TrimRight(endpoint.BaseURL, "/") == strings.TrimRight(values["AGENT_CLOUD_ASSIST_URL"], "/"),
		}
	}
	return result
}

func readValues(path string) (map[string]string, error) {
	if path == "" {
		return map[string]string{}, os.ErrNotExist
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
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

func writeValues(path string, values map[string]string) error {
	if path == "" {
		return errors.New("Local Agent config path is required")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	temporary, err := os.CreateTemp(filepath.Dir(path), ".local.env-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	for _, key := range keys {
		if _, err := fmt.Fprintf(temporary, "%s=%s\n", key, values[key]); err != nil {
			temporary.Close()
			return err
		}
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}
