package manipulation_test

import (
	"fmt"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestControllerPreparedLongRoutePreservesEveryNavigationAndVerification(t *testing.T) {
	for _, preparation := range []string{"", "agent_pre_position", "controller"} {
		t.Run(preparation, func(t *testing.T) {
			task := manipulation.GroundedTask{TaskID: "long-inspection", RobotID: "ros2-device", Action: manipulation.ActionHomeRoute, NavigationPreparation: preparation, StepIDPrefix: "stage-"}
			for i := 0; i < 40; i++ {
				task.RouteRooms = append(task.RouteRooms, fmt.Sprintf("inspection-%02d", i))
				task.RouteGoals = append(task.RouteGoals, []float64{float64(i), 1, 2, 1, 0, 0, 0})
			}
			plan := manipulation.Plan(task, time.Now().Add(time.Hour))
			known := map[string]bool{}
			navigation, verification, preparationCount := 0, 0, 0
			for _, step := range plan.Steps {
				for _, dependency := range step.DependsOn {
					if !known[dependency] {
						t.Fatalf("missing dependency %s", dependency)
					}
				}
				if known[step.ID] {
					t.Fatalf("duplicate step %s", step.ID)
				}
				known[step.ID] = true
				switch step.Skill {
				case "navigation.navigate":
					navigation++
					if step.Arguments["goalPose"].([]float64)[2] != 2 {
						t.Fatal("3D target flattened")
					}
				case "verify_arrival":
					verification++
				case "navigation.pre_position":
					preparationCount++
				}
			}
			wantPreparation := 1
			if preparation == "controller" {
				wantPreparation = 0
			}
			if navigation != 40 || verification != 40 || preparationCount != wantPreparation {
				t.Fatalf("nav=%d verify=%d prep=%d", navigation, verification, preparationCount)
			}
		})
	}
}
