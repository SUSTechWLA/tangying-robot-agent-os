package agentcontext

// ModelCall is transport telemetry, never task authority or proof of a physical
// effect. DurationMS measures the complete non-streaming call, not TTFT. Usage
// comes from the provider; it is not an independent measurement of GPU work.
type ModelCall struct {
	SchemaVersion         string      `json:"schema_version"`
	CallID                string      `json:"call_id,omitempty"`
	Stage                 string      `json:"stage,omitempty"`
	Model                 string      `json:"model,omitempty"`
	EndpointSHA256        string      `json:"endpoint_sha256,omitempty"`
	OutputTokens          int         `json:"output_tokens,omitempty"`
	PromptTemplateVersion string      `json:"prompt_template_version"`
	ToolsSHA256           string      `json:"tools_sha256"`
	CacheMode             string      `json:"cache_mode"`
	CacheIdentitySHA256   string      `json:"cache_identity_sha256,omitempty"`
	StartedAt             string      `json:"started_at"`
	DurationMS            int64       `json:"duration_ms"`
	RequestBytes          int         `json:"request_bytes"`
	ResponseBytes         int         `json:"response_bytes"`
	HTTPStatus            int         `json:"http_status,omitempty"`
	Outcome               string      `json:"outcome"`
	FinishReason          string      `json:"finish_reason,omitempty"`
	ResponseTruncated     bool        `json:"response_truncated,omitempty"`
	UsageLogStatus        string      `json:"usage_log_status,omitempty"`
	ResponseSHA256        string      `json:"response_sha256,omitempty"`
	UsageStatus           string      `json:"usage_status"`
	Usage                 *ModelUsage `json:"usage,omitempty"`
}

// Pointers preserve absent versus explicitly reported zero. Partial usage is
// valid; callers must use only fields actually reported by this endpoint.
type ModelUsage struct {
	PromptTokens     *int64 `json:"prompt_tokens,omitempty"`
	CompletionTokens *int64 `json:"completion_tokens,omitempty"`
	TotalTokens      *int64 `json:"total_tokens,omitempty"`
	CachedTokens     *int64 `json:"cached_tokens,omitempty"`
	CacheWriteTokens *int64 `json:"cache_write_tokens,omitempty"`
}
