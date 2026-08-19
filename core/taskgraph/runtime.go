package taskgraph

import (
	"fmt"
	"sync"
)

type NodeStatus string

const (
	NodePending   NodeStatus = "PENDING"
	NodeReady     NodeStatus = "READY"
	NodeRunning   NodeStatus = "RUNNING"
	NodeSucceeded NodeStatus = "SUCCEEDED"
	NodeFailed    NodeStatus = "FAILED"
	NodeCancelled NodeStatus = "CANCELLED"
)

// RuntimeNode is the mutable execution state of one TaskPlan node.
type RuntimeNode struct {
	Step       SkillStep
	Status     NodeStatus
	Remaining  int
	Dependents []string
}

// GraphRuntime is an event-driven task graph executor state machine.
//
// It is intentionally separate from compiler.ExecutionGraph: the compiler
// validates static shape once, while GraphRuntime refreshes dependents when a
// node reaches a terminal state. This is the primitive needed for multi-robot
// plans where node N on robot A may become ready only after nodes on robot B
// complete.
type GraphRuntime struct {
	mu     sync.Mutex
	nodes  map[string]*RuntimeNode
	ready  []string
	status map[string]NodeStatus
}

func NewGraphRuntime(plan TaskPlan) (*GraphRuntime, error) {
	if err := plan.ValidateShape(); err != nil {
		return nil, err
	}
	graph := &GraphRuntime{
		nodes:  map[string]*RuntimeNode{},
		status: map[string]NodeStatus{},
	}
	dependents := map[string][]string{}
	for _, step := range plan.Steps {
		graph.nodes[step.ID] = &RuntimeNode{
			Step:      step,
			Remaining: len(step.DependsOn),
		}
		graph.status[step.ID] = NodePending
		for _, dependency := range step.DependsOn {
			dependents[dependency] = append(dependents[dependency], step.ID)
		}
	}
	for _, step := range plan.Steps {
		node := graph.nodes[step.ID]
		node.Dependents = append([]string(nil), dependents[step.ID]...)
		if node.Remaining == 0 {
			graph.ready = append(graph.ready, step.ID)
			graph.status[step.ID] = NodeReady
		}
	}
	return graph, nil
}

// Ready returns nodes whose dependencies have all succeeded, in plan order.
func (g *GraphRuntime) Ready() []string {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]string(nil), g.ready...)
}

func (g *GraphRuntime) Status(id string) (NodeStatus, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	status, ok := g.status[id]
	return status, ok
}

func (g *GraphRuntime) Step(id string) (SkillStep, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	node, ok := g.nodes[id]
	if !ok {
		return SkillStep{}, false
	}
	return node.Step, true
}

func (g *GraphRuntime) MarkRunning(id string) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.markRunningLocked(id)
}

func (g *GraphRuntime) markRunningLocked(id string) error {
	status, ok := g.status[id]
	if !ok {
		return fmt.Errorf("unknown node %s", id)
	}
	if status != NodeReady {
		return fmt.Errorf("node %s is %s, not READY", id, status)
	}
	g.status[id] = NodeRunning
	return nil
}

// MarkSucceeded marks a node succeeded and refreshes its dependents. It
// returns the nodes that became READY as a result of this completion.
func (g *GraphRuntime) MarkSucceeded(id string) ([]string, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	status, ok := g.status[id]
	if !ok {
		return nil, fmt.Errorf("unknown node %s", id)
	}
	if status != NodeRunning {
		return nil, fmt.Errorf("node %s is %s, cannot succeed", id, status)
	}
	g.status[id] = NodeSucceeded
	var newlyReady []string
	for _, dependent := range g.nodes[id].Dependents {
		node := g.nodes[dependent]
		if g.status[dependent] != NodePending {
			continue
		}
		node.Remaining--
		if node.Remaining == 0 {
			g.status[dependent] = NodeReady
			g.ready = append(g.ready, dependent)
			newlyReady = append(newlyReady, dependent)
		}
	}
	return newlyReady, nil
}

func (g *GraphRuntime) MarkFailed(id string, reason string) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	status, ok := g.status[id]
	if !ok {
		return fmt.Errorf("unknown node %s", id)
	}
	if status != NodeRunning && status != NodeReady {
		return fmt.Errorf("node %s is %s, cannot fail", id, status)
	}
	g.status[id] = NodeFailed
	// Fail-closed: dependents remain blocked and never become READY.
	return nil
}

func (g *GraphRuntime) MarkCancelled(id string) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if _, ok := g.status[id]; !ok {
		return fmt.Errorf("unknown node %s", id)
	}
	g.status[id] = NodeCancelled
	return nil
}
