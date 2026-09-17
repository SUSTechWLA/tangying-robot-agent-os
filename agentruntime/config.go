package agentruntime

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Duration is a time span that can be written in configuration as a Go duration
// string such as "5s". It is a named type rather than time.Duration so that the
// zero value keeps meaning "unset, use the default" instead of "zero seconds",
// which for a health interval would be a busy loop.
type Duration time.Duration

// String renders the span in the form configuration uses.
func (d Duration) String() string { return time.Duration(d).String() }

// Or returns the span, or fallback when unset.
func (d Duration) Or(fallback time.Duration) time.Duration {
	if d <= 0 {
		return fallback
	}
	return time.Duration(d)
}

// TaskAgentName is the configuration name of the execution agent. It is
// duplicated from the edge package deliberately: this package must not import
// the agent implementations it hosts, or adding an agent would mean changing the
// runtime.
const TaskAgentName = "task"

// OpsAgentName is the configuration name of the observing agent.
const OpsAgentName = "ops"

// Config selects which agents run.
//
// The default enables exactly two agents. The whole point of the field is that
// EvalAgent, ExperienceAgent and EscalationAgent can be added later by shipping
// the implementation and naming it here, with no change to this package or to
// the orchestrator.
type Config struct {
	// Enabled lists the agents to run, by name. Order does not matter; agents
	// are started in registration order so behaviour is reproducible.
	Enabled []string
	// QueueCapacity overrides the per-subscriber event queue capacity. Zero
	// means the runtime default.
	QueueCapacity int
	// HealthInterval is how often each agent's health is checked. Zero means
	// DefaultHealthInterval.
	HealthInterval Duration
}

// DefaultConfig enables the agents this version ships. Everything else is
// registered but not started, so an operator can see what exists without running
// it.
//
// The recovery agent is enabled because it is read-only in this version: it
// publishes a plan and executes nothing. Its model route is a separate switch
// (RecoveryAgent.Model), so enabling the agent does not change what the system
// proposes.
func DefaultConfig() Config {
	return Config{Enabled: []string{TaskAgentName, OpsAgentName, RecoveryAgentName}}
}

// ExecutableConfig enables only the execution agent.
//
// It is a supported configuration rather than a test fixture: an operator
// debugging whether an observation is changing behaviour can turn observation
// off and change nothing else. Execution must be byte-for-byte the same either
// way, and the tests assert that.
func ExecutableConfig() Config {
	return Config{Enabled: []string{TaskAgentName}}
}

// ErrInvalidConfig reports a configuration that cannot be honoured.
var ErrInvalidConfig = errors.New("invalid agent runtime configuration")

// normalize validates and canonicalizes configuration.
func (c Config) normalize() (Config, error) {
	normalized := c
	normalized.Enabled = nil
	seen := map[string]bool{}
	for _, raw := range c.Enabled {
		name := strings.TrimSpace(raw)
		if name == "" {
			return Config{}, fmt.Errorf("%w: an enabled agent name is empty", ErrInvalidConfig)
		}
		if name != raw {
			return Config{}, fmt.Errorf("%w: enabled agent name %q has surrounding whitespace", ErrInvalidConfig, raw)
		}
		if seen[name] {
			return Config{}, fmt.Errorf("%w: agent %s is enabled twice", ErrInvalidConfig, name)
		}
		seen[name] = true
		normalized.Enabled = append(normalized.Enabled, name)
	}
	if normalized.QueueCapacity < 0 {
		return Config{}, fmt.Errorf("%w: queue capacity %d is negative", ErrInvalidConfig, normalized.QueueCapacity)
	}
	if normalized.HealthInterval < 0 {
		return Config{}, fmt.Errorf("%w: health interval %s is negative", ErrInvalidConfig, normalized.HealthInterval)
	}
	return normalized, nil
}

// Requires reports whether an agent name is enabled.
func (c Config) Requires(name string) bool {
	for _, enabled := range c.Enabled {
		if enabled == name {
			return true
		}
	}
	return false
}

// Sorted returns the enabled names sorted, for stable reporting.
func (c Config) Sorted() []string {
	names := append([]string(nil), c.Enabled...)
	sort.Strings(names)
	return names
}
