package compiler

import (
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

type Node struct {
	Step taskgraph.SkillStep `json:"step"`
}

type ExecutionGraph struct {
	TaskID string          `json:"taskId"`
	Nodes  map[string]Node `json:"nodes"`
	Order  []string        `json:"order"`
}

type Compiler struct{}

func New() *Compiler { return &Compiler{} }

func (c *Compiler) Compile(plan taskgraph.TaskPlan) (ExecutionGraph, error) {
	if err := plan.ValidateShape(); err != nil {
		return ExecutionGraph{}, err
	}
	nodes := make(map[string]Node, len(plan.Steps))
	indegree := make(map[string]int, len(plan.Steps))
	dependents := make(map[string][]string, len(plan.Steps))
	for _, step := range plan.Steps {
		nodes[step.ID] = Node{Step: step}
		indegree[step.ID] = len(step.DependsOn)
		for _, dependency := range step.DependsOn {
			dependents[dependency] = append(dependents[dependency], step.ID)
		}
	}
	order := make([]string, 0, len(plan.Steps))
	for len(order) < len(plan.Steps) {
		advanced := false
		for _, step := range plan.Steps {
			if indegree[step.ID] != 0 || contains(order, step.ID) {
				continue
			}
			order = append(order, step.ID)
			for _, dependent := range dependents[step.ID] {
				indegree[dependent]--
			}
			advanced = true
		}
		if !advanced {
			return ExecutionGraph{}, fmt.Errorf("task graph contains a cycle")
		}
	}
	return ExecutionGraph{TaskID: plan.ID, Nodes: nodes, Order: order}, nil
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
