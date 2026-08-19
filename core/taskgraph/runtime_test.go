package taskgraph

import "testing"

func TestGraphRuntimeRefreshesDependentsAfterNodeCompletion(t *testing.T) {
	plan := TaskPlan{Steps: []SkillStep{
		{ID: "robot1-observe", Skill: "observe_scene", RobotID: "robot-1"},
		{ID: "robot2-ready", Skill: "navigate", RobotID: "robot-2", DependsOn: []string{"robot1-observe"}},
		{ID: "robot1-pick", Skill: "manipulation.pick", RobotID: "robot-1", DependsOn: []string{"robot1-observe"}},
		{ID: "robot2-place", Skill: "manipulation.place", RobotID: "robot-2", DependsOn: []string{"robot2-ready", "robot1-pick"}},
	}}
	graph, err := NewGraphRuntime(plan)
	if err != nil {
		t.Fatal(err)
	}
	if ready := graph.Ready(); len(ready) != 1 || ready[0] != "robot1-observe" {
		t.Fatalf("initial ready = %v", ready)
	}
	if err := graph.MarkRunning("robot1-observe"); err != nil {
		t.Fatal(err)
	}
	refreshed, err := graph.MarkSucceeded("robot1-observe")
	if err != nil {
		t.Fatal(err)
	}
	if len(refreshed) != 2 {
		t.Fatalf("refreshed nodes = %v", refreshed)
	}
	if status, _ := graph.Status("robot2-ready"); status != NodeReady {
		t.Fatalf("robot2-ready status = %s", status)
	}
	if status, _ := graph.Status("robot2-place"); status != NodePending {
		t.Fatalf("robot2-place should remain pending, got %s", status)
	}
}

func TestGraphRuntimeFailsClosedAndDoesNotRefreshDependents(t *testing.T) {
	plan := TaskPlan{Steps: []SkillStep{
		{ID: "observe", Skill: "observe_scene"},
		{ID: "pick", Skill: "manipulation.pick", DependsOn: []string{"observe"}},
	}}
	graph, _ := NewGraphRuntime(plan)
	if err := graph.MarkFailed("observe", "sensor offline"); err != nil {
		t.Fatal(err)
	}
	if status, _ := graph.Status("pick"); status != NodePending {
		t.Fatalf("pick status = %s", status)
	}
}
