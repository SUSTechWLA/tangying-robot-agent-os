package manipulation

import "fmt"

// HomeWaypoints mirrors the commissioned scene contract. A physical adapter
// may replace these with RTAB-Map map-frame poses after localization; the
// language layer never invents a pose from object coordinates.
var HomeWaypoints = map[string][]float64{
	"living_room":   {0.0, -1.25, 0.035, 0.7071067812, 0, 0, 0.7071067812},
	"home_corridor": {0.0, 1.85, 0.035, 0.7071067812, 0, 0, 0.7071067812},
	"kitchen":       {2.05, 3.35, 0.035, 0.7071067812, 0, 0, 0.7071067812},
	"bedroom":       {-2.05, 3.35, 0.035, 0.7071067812, 0, 0, 0.7071067812},
	"bathroom":      {-2.05, 6.55, 0.035, 0.7071067812, 0, 0, 0.7071067812},
}

func HomeRouteGoals(rooms []string) ([][]float64, error) {
	goals := make([][]float64, 0, len(rooms))
	for _, room := range rooms {
		goal, ok := HomeWaypoints[room]
		if !ok {
			return nil, fmt.Errorf("unknown home room %q", room)
		}
		goals = append(goals, append([]float64(nil), goal...))
	}
	return goals, nil
}
