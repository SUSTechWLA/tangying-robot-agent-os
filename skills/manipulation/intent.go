package manipulation

const (
	ActionPickAndPlace = "pick_and_place"
	ActionFetch        = "fetch"
	ActionHomeRoute    = "home_route"
	// ActionHomeManipulation is a commissioned household transfer: the robot
	// navigates to a room, performs an RGB-D grounded manipulation, then may
	// return to the requested room. It keeps navigation and arm tools separate
	// so each physical effect remains independently auditable and recoverable.
	ActionHomeManipulation = "home_manipulation"
)

const (
	CategoryStorageBin   = "storage_bin"
	CategoryDeliveryTray = "delivery_tray"
	CategoryHandoffZone  = "handoff_zone"
	CategoryTargetZone   = "target_zone"
)

type Intent struct {
	Action      string         `json:"action"`
	RobotID     string         `json:"robotId,omitempty"`
	Object      EntitySelector `json:"object"`
	Source      EntitySelector `json:"source,omitempty"`
	Destination EntitySelector `json:"destination"`
	Constraints Constraints    `json:"constraints"`
	// RouteRooms is an ordered list of commissioned home-scene rooms. It is
	// resolved to waypoints by the navigation adapter, never to simulator truth.
	RouteRooms    []string `json:"routeRooms,omitempty"`
	ReturnToStart bool     `json:"returnToStart,omitempty"`
	// Sequence contains all intents when one user sentence requests a chain of
	// tasks, for example "把红色杯子放进右侧收纳盒，然后把蓝色杯子拿过来".
	// When empty, the receiver falls back to this single intent.
	Sequence []Intent `json:"sequence,omitempty"`
}

// Tasks returns the ordered list of intents represented by this request.
func (i Intent) Tasks() []Intent {
	if len(i.Sequence) == 0 {
		return []Intent{i}
	}
	return append([]Intent(nil), i.Sequence...)
}

type EntitySelector struct {
	Category   string            `json:"category"`
	Attributes map[string]string `json:"attributes,omitempty"`
	Relation   string            `json:"relation,omitempty"`
}

type Constraints struct {
	KeepUpright bool `json:"keepUpright"`
	AvoidHumans bool `json:"avoidHumans"`
}

type SceneRef struct {
	ID         string  `json:"id"`
	Confidence float64 `json:"confidence"`
}

type GroundedTask struct {
	TaskID       string   `json:"taskId"`
	RobotID      string   `json:"robotId,omitempty"`
	Action       string   `json:"action,omitempty"`
	Object       SceneRef `json:"object"`
	Destination  SceneRef `json:"destination"`
	KeepUpright  bool     `json:"keepUpright"`
	StepIDPrefix string   `json:"stepIdPrefix,omitempty"`
	// NavigationGoal is supplied by a declared mobile adapter's local planner,
	// never invented from object XYZ by the language model.
	NavigationGoal []float64   `json:"navigationGoal,omitempty"`
	RouteRooms     []string    `json:"routeRooms,omitempty"`
	RouteGoals     [][]float64 `json:"routeGoals,omitempty"`
	ReturnToStart  bool        `json:"returnToStart,omitempty"`
}
