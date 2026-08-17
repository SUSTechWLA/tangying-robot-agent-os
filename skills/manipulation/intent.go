package manipulation

type Intent struct {
	Action      string         `json:"action"`
	Object      EntitySelector `json:"object"`
	Destination EntitySelector `json:"destination"`
	Constraints Constraints    `json:"constraints"`
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
	TaskID      string   `json:"taskId"`
	Object      SceneRef `json:"object"`
	Destination SceneRef `json:"destination"`
	KeepUpright bool     `json:"keepUpright"`
}
