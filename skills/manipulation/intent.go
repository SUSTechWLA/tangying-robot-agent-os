package manipulation

const (
	ActionPickAndPlace = "pick_and_place"
	ActionFetch        = "fetch"
)

const (
	CategoryStorageBin   = "storage_bin"
	CategoryDeliveryTray = "delivery_tray"
)

type Intent struct {
	Action      string         `json:"action"`
	Object      EntitySelector `json:"object"`
	Destination EntitySelector `json:"destination"`
	Constraints Constraints    `json:"constraints"`
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
	Action       string   `json:"action,omitempty"`
	Object       SceneRef `json:"object"`
	Destination  SceneRef `json:"destination"`
	KeepUpright  bool     `json:"keepUpright"`
	StepIDPrefix string   `json:"stepIdPrefix,omitempty"`
}
