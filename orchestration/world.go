package orchestration

import (
	"encoding/json"
	"fmt"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"math"
	"sort"
	"strings"
)

// World is the part of the runtime state a plan has to be consistent with.
//
// # Why the planner needs this
//
// A plan produced without it is not merely less informed; it can be impossible.
// The reference deployment's robot starts in the living room, the mug is in the
// kitchen four metres away behind a wall, and a plan that begins with
// `resolve_targets` asks the camera to find a mug it cannot see. The step fails,
// the task enters recovery, and every layer below behaves correctly — which is why
// the fault survived so long: the failure looked like a perception problem and was
// a planning one.
//
// The model did not omit the navigation step out of carelessness. Nothing it was
// given said where the robot was or where the mug was, so "look at the mug" was a
// perfectly reasonable instruction to write down. A planner that cannot see the
// world will produce plans for a world it imagined.
type World struct {
	// GroundedOnly prevents legacy telemetry from becoming model-facing facts.
	GroundedOnly bool
	StateReports []string
	// RobotRoom is where the robot is now, named the way the navigation layer
	// names places ("kitchen", "living_room"). Empty means unknown, which is
	// reported as unknown rather than guessed.
	RobotRoom string
	// Objects are where each object category was last recorded.
	Objects []ObjectPlace
}

// ObjectPlace is one category's last known room.
type ObjectPlace struct {
	Category string `json:"category"`
	Room     string `json:"room"`
}

// Known reports whether the world carries enough to plan against.
//
// It exists so callers can say "we do not know" rather than passing a zero value
// that reads as "the robot is nowhere and there is nothing anywhere". A planner
// given a zero World should still work — it simply cannot add the navigation step —
// and the prompt must not claim otherwise.
func (w World) Known() bool {
	return strings.TrimSpace(w.RobotRoom) != "" || len(w.Objects) > 0
}

// Rooms returns the distinct rooms an object of this category is known to be in.
func (w World) Rooms(category string) []string {
	wanted := strings.ToLower(strings.TrimSpace(category))
	rooms := make([]string, 0, 1)
	seen := map[string]bool{}
	for _, place := range w.Objects {
		if strings.ToLower(strings.TrimSpace(place.Category)) != wanted {
			continue
		}
		room := strings.TrimSpace(place.Room)
		if room == "" || seen[room] {
			continue
		}
		seen[room] = true
		rooms = append(rooms, room)
	}
	sort.Strings(rooms)
	return rooms
}

// Describe renders the world for a planning prompt.
//
// It is written as sentences rather than as JSON because the instruction that
// matters is a constraint, not a datum: **the robot cannot see through walls**.
// Stated as a field, a model may treat it as optional context; stated as a rule
// with a reason, it changes what gets planned.
func (w World) Describe() string {
	if w.GroundedOnly {
		if mode := agentcontext.Mode(); mode != "legacy" {
			doc := agentcontext.Document{SchemaVersion: agentcontext.Version, Stage: "planning", Role: "planning", Goal: "依据边缘验证报告编排下一任务", Constraints: []string{"报告仅描述原任务采集时的状态；计划版本、机器人身份或 UTC 有效期缺失时不得推定适用于当前决策。", "SUCCESS 不等于物理成功；先核对作用域与新鲜度，未知则先观察。"}}
			for i, raw := range w.StateReports {
				var header map[string]any
				if json.Unmarshal([]byte(raw), &header) != nil {
					continue
				}
				taskID, _ := header["task_id"].(string)
				if record, err := agentcontext.GroundedRecord(raw, taskID, ""); err == nil {
					record.ID = fmt.Sprintf("report:%d:%s", i, record.ID)
					doc.Records = append(doc.Records, record)
				}
			}
			if text, err := agentcontext.Render(doc, mode); err == nil {
				return text
			}
		}
		return "物理事实只允许来自以下边缘 StateReport。报告描述采集时的状态；未知、缺失或过期状态必须先观察。" +
			"工具 SUCCESS 不是物理成功。模型只能规划和建议，不能更改报告结论或批准重试。\n" + strings.Join(w.StateReports, "\n")
	}
	if !w.Known() {
		return "Current state: unknown. The robot's location and the objects' rooms were not available, " +
			"so no navigation step can be added for you. Plan as if everything needed is within reach, " +
			"and say so in the plan's goal if that is an assumption."
	}
	var builder strings.Builder
	builder.WriteString("Current state:\n")
	if room := strings.TrimSpace(w.RobotRoom); room != "" {
		fmt.Fprintf(&builder, "- The robot is in the %s.\n", room)
	} else {
		builder.WriteString("- The robot's location is unknown.\n")
	}
	if len(w.Objects) > 0 {
		places := append([]ObjectPlace(nil), w.Objects...)
		sort.SliceStable(places, func(i, j int) bool {
			if places[i].Category != places[j].Category {
				return places[i].Category < places[j].Category
			}
			return places[i].Room < places[j].Room
		})
		builder.WriteString("- Known object locations: ")
		parts := make([]string, 0, len(places))
		for _, place := range places {
			parts = append(parts, fmt.Sprintf("%s in the %s", place.Category, place.Room))
		}
		builder.WriteString(strings.Join(parts, ", "))
		builder.WriteString(".\n")
	} else {
		builder.WriteString("- No object locations are known.\n")
	}
	builder.WriteString(
		"- **The robot cannot see through walls.** A skill that looks for or manipulates an object " +
			"only works when the robot is in a room the object is known to be in. If the object is " +
			"elsewhere, the plan must navigate first; the runtime resolves room names to coordinates.")
	return builder.String()
}

// WorldFrom reads the runtime state a plan is formed against.
//
// It takes the robot state as a map rather than a typed snapshot so this package
// does not depend on the transport that carried it: the same map arrives from
// telemetry, from a replay, and from a test fixture, and a planner that could only
// be planned for over one of those would be a planner nobody could test.
//
// # What it refuses to guess
//
// Every field is optional and an unreadable one leaves the world partly unknown
// rather than defaulted. A robot placed in a room it is not in would produce a
// confidently wrong plan, which is worse than a plan that admits it lacks the
// navigation it needs.
func WorldFrom(robotState map[string]any) World {
	if len(robotState) == 0 {
		return World{}
	}
	world := World{
		RobotRoom: roomOf(robotState),
		Objects:   placesOf(robotState),
	}
	return world
}

// roomOf names the room the robot is in, by matching its base pose against the
// commissioned navigation goals.
//
// The pose is metres in the map frame; the goals are the named places the
// navigation layer already resolves room names to. Comparing them is how a
// coordinate becomes a name without a second place-name table drifting from the
// first.
func roomOf(robotState map[string]any) string {
	pose, ok := robotState["base_pose"].([]any)
	if !ok || len(pose) < 2 {
		return ""
	}
	x, xOK := toFloat(pose[0])
	y, yOK := toFloat(pose[1])
	if !xOK || !yOK {
		return ""
	}
	navigation, ok := robotState["semantic_navigation"].(map[string]any)
	if !ok {
		return ""
	}
	goals, ok := navigation["goals"].(map[string]any)
	if !ok {
		return ""
	}
	best, bestDistance := "", 0.0
	for room, raw := range goals {
		goal, ok := raw.([]any)
		if !ok || len(goal) < 2 {
			continue
		}
		goalX, xOK := toFloat(goal[0])
		goalY, yOK := toFloat(goal[1])
		if !xOK || !yOK {
			continue
		}
		distance := math.Hypot(goalX-x, goalY-y)
		if best == "" || distance < bestDistance {
			best, bestDistance = room, distance
		}
	}
	// A robot a long way from every goal is not "in" the nearest one; saying so
	// would put a confident wrong room into the prompt. The threshold is generous
	// because a room is metres across, and it is a constant rather than a
	// per-deployment setting because a deployment that needs a different one has a
	// map whose goals are wrong.
	if best == "" || bestDistance > roomMatchRadiusM {
		return ""
	}
	return best
}

// roomMatchRadiusM is how far the robot may be from a named goal and still count
// as being in that room.
const roomMatchRadiusM = 4.0

// placesOf reads where each object category was last recorded.
func placesOf(robotState map[string]any) []ObjectPlace {
	raw, ok := robotState["semantic_objects"].([]any)
	if !ok {
		return nil
	}
	places := make([]ObjectPlace, 0, len(raw))
	seen := map[string]bool{}
	for _, entry := range raw {
		object, ok := entry.(map[string]any)
		if !ok {
			continue
		}
		category, _ := object["category"].(string)
		room, _ := object["workArea"].(string)
		category, room = strings.TrimSpace(category), strings.TrimSpace(room)
		if category == "" || room == "" {
			continue
		}
		key := strings.ToLower(category) + "@" + room
		if seen[key] {
			continue
		}
		seen[key] = true
		places = append(places, ObjectPlace{Category: category, Room: room})
	}
	return places
}

func toFloat(value any) (float64, bool) {
	switch typed := value.(type) {
	case float64:
		if math.IsNaN(typed) || math.IsInf(typed, 0) {
			return 0, false
		}
		return typed, true
	case int:
		return float64(typed), true
	case int64:
		return float64(typed), true
	}
	return 0, false
}
