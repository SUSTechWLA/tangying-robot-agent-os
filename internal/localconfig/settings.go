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
)

type Settings struct {
	path   string
	mu     sync.RWMutex
	status console.ConfigStatus
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
	}
	if settings.status.Provider == "" {
		settings.status.Provider = "deterministic"
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
	apiKey := strings.TrimSpace(input.APIKey)
	if apiKey == "" {
		apiKey = values["AGENT_API_KEY"]
	}
	baseURL := strings.TrimSpace(input.BaseURL)
	model := strings.TrimSpace(input.Model)
	if provider == "openai" && (baseURL == "" || model == "" || apiKey == "") {
		return errors.New("openai provider requires baseUrl, model, and apiKey")
	}
	values["AGENT_PROVIDER"] = provider
	values["AGENT_BASE_URL"] = baseURL
	values["AGENT_MODEL"] = model
	values["AGENT_API_KEY"] = apiKey
	if err := writeValues(s.path, values); err != nil {
		return err
	}
	s.status = console.ConfigStatus{
		Provider: provider, BaseURL: baseURL, Model: model, HasAPIKey: apiKey != "", RestartRequired: true,
	}
	return nil
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
