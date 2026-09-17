package agentruntime

import (
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// Registry knows which agents exist and which of them are enabled.
//
// Registration and enablement are separate on purpose. An agent being available
// in a build is not a decision to run it: the requirement is that adding an
// EvalAgent, ExperienceAgent or EscalationAgent later needs no change to core
// code, and the operative part of that is being able to ship the agent and still
// choose, by configuration, whether it runs.
type Registry struct {
	agents map[string]agentcontract.Agent
	order  []string
	config Config
}

// ErrAgentAlreadyRegistered is returned when two agents claim one name. Names
// attribute durable events, so a collision would make a replay ambiguous.
var ErrAgentAlreadyRegistered = errors.New("agent already registered")

// NewRegistry creates an empty registry with the given configuration. The
// configuration is validated here rather than at Start, so a typo in an enabled
// name is reported by the code that built the registry instead of deep inside
// startup.
func NewRegistry(config Config) (*Registry, error) {
	normalized, err := config.normalize()
	if err != nil {
		return nil, err
	}
	return &Registry{agents: map[string]agentcontract.Agent{}, config: normalized}, nil
}

// Register adds an agent. It refuses a duplicate name, an empty name, and an
// agent that declares no capabilities: an agent that does nothing is more likely
// a wiring mistake than a deliberate participant.
func (r *Registry) Register(agent agentcontract.Agent) error {
	if agent == nil {
		return errors.New("cannot register a nil agent")
	}
	name := strings.TrimSpace(agent.Name())
	if name == "" {
		return errors.New("cannot register an agent with an empty name")
	}
	if name != agent.Name() {
		return fmt.Errorf("agent name %q has surrounding whitespace; it is a configuration key and must be exact", agent.Name())
	}
	if len(agent.Capabilities()) == 0 {
		return fmt.Errorf("agent %s declares no capabilities", name)
	}
	for _, pattern := range agent.Subscriptions() {
		if !agentcontract.KnownPattern(pattern) {
			return fmt.Errorf("agent %s subscribes to unknown topic pattern %q", name, pattern)
		}
	}
	if _, exists := r.agents[name]; exists {
		return fmt.Errorf("%w: %s", ErrAgentAlreadyRegistered, name)
	}
	r.agents[name] = agent
	// Registration order is kept: it is the tie-breaking rule when two agents
	// have equal priority, and a stable order keeps behaviour reproducible.
	r.order = append(r.order, name)
	return nil
}

// Discover returns a registered agent by name.
func (r *Registry) Discover(name string) (agentcontract.Agent, bool) {
	agent, ok := r.agents[name]
	return agent, ok
}

// Registered reports every registered agent name in registration order.
func (r *Registry) Registered() []string {
	return append([]string(nil), r.order...)
}

// Enabled reports the enabled agent names in registration order.
func (r *Registry) Enabled() []string {
	enabled := make([]string, 0, len(r.config.Enabled))
	for _, name := range r.order {
		for _, candidate := range r.config.Enabled {
			if candidate == name {
				enabled = append(enabled, name)
				break
			}
		}
	}
	return enabled
}

// EnabledAgents returns the enabled agents in registration order.
func (r *Registry) EnabledAgents() []agentcontract.Agent {
	names := r.Enabled()
	agents := make([]agentcontract.Agent, 0, len(names))
	for _, name := range names {
		agents = append(agents, r.agents[name])
	}
	return agents
}

// Config returns the validated configuration.
func (r *Registry) Config() Config { return r.config }

// Validate reports configuration that cannot be satisfied by what is
// registered.
//
// An enabled name with no registered agent is refused rather than skipped. The
// alternative — starting with a subset of what was asked for and saying nothing
// — is how an operator ends up believing an observer is running when it is not.
func (r *Registry) Validate() error {
	var missing []string
	for _, name := range r.config.Enabled {
		if _, ok := r.agents[name]; !ok {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return fmt.Errorf("%w: enabled agents are not registered: %s",
			ErrUnknownAgent, strings.Join(missing, ", "))
	}
	return nil
}

// ErrUnknownAgent means configuration enables a name that is not registered.
var ErrUnknownAgent = errors.New("unknown agent")

// Disabled reports registered agents that the configuration leaves off. It is
// reported rather than ignored so an operator can see that a shipped agent is
// present but not running.
func (r *Registry) Disabled() []string {
	enabled := map[string]bool{}
	for _, name := range r.config.Enabled {
		enabled[name] = true
	}
	disabled := make([]string, 0, len(r.order))
	for _, name := range r.order {
		if !enabled[name] {
			disabled = append(disabled, name)
		}
	}
	return disabled
}
