package actionloop

import (
	"encoding/json"
	"errors"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
)

// Bump when fixed instructions change. The actual rules are hashed too, so an
// omitted version bump cannot cause this client to reuse the same cache hint.
const PromptTemplateVersion = "actionloop-rules.v2"

func (d *LLMDecider) cacheIdentity(request Request, tools []toolSchema) (string, string, error) {
	if err := d.Cache.Validate(); err != nil {
		return "", "", err
	}
	wire, err := json.Marshal(tools)
	if err != nil {
		return "", "", err
	}
	toolsHash := agentcontext.Hash(string(wire))
	if d.Cache.EffectiveMode() == "off" {
		return "", toolsHash, nil
	}
	scope := request.ContextSnapshot.Scope
	if scope.RobotID == "" && scope.TaskID == "" {
		return "", "", errors.New("cache hints require a concrete robot or task scope")
	}
	// This is a routing/partition hint, not a secret, ACL, semantic context hash,
	// request hash, or indication that any backend cached a token.
	identity, err := json.Marshal(struct {
		Version     string             `json:"version"`
		Namespace   string             `json:"namespace"`
		Endpoint    string             `json:"endpoint"`
		Model       string             `json:"model"`
		Role        string             `json:"role"`
		Scope       agentcontext.Scope `json:"scope"`
		ToolsSHA256 string             `json:"tools_sha256"`
		RulesSHA256 string             `json:"rules_sha256"`
	}{PromptTemplateVersion, d.Cache.Namespace, strings.TrimRight(d.BaseURL, "/"), d.Model, request.Role, scope, toolsHash, agentcontext.Hash(systemPrompt(request))})
	if err != nil {
		return "", "", err
	}
	return agentcontext.Hash(string(identity)), toolsHash, nil
}

func modelUsage(raw json.RawMessage) (*agentcontext.ModelUsage, string) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, "missing"
	}
	var wire struct {
		PromptTokens     *int64 `json:"prompt_tokens"`
		CompletionTokens *int64 `json:"completion_tokens"`
		TotalTokens      *int64 `json:"total_tokens"`
		Details          *struct {
			CachedTokens     *int64 `json:"cached_tokens"`
			CacheWriteTokens *int64 `json:"cache_write_tokens"`
		} `json:"prompt_tokens_details"`
	}
	// Do not let malformed optional accounting discard a valid tool decision.
	if err := json.Unmarshal(raw, &wire); err != nil {
		return nil, "invalid"
	}
	usage := &agentcontext.ModelUsage{PromptTokens: wire.PromptTokens, CompletionTokens: wire.CompletionTokens, TotalTokens: wire.TotalTokens}
	if wire.Details != nil {
		usage.CachedTokens = wire.Details.CachedTokens
		usage.CacheWriteTokens = wire.Details.CacheWriteTokens
	}
	found := false
	for _, value := range []*int64{usage.PromptTokens, usage.CompletionTokens, usage.TotalTokens, usage.CachedTokens, usage.CacheWriteTokens} {
		if value == nil {
			continue
		}
		found = true
		if *value < 0 || *value > (1<<53)-1 {
			return nil, "invalid"
		}
	}
	if usage.PromptTokens != nil && usage.CachedTokens != nil && *usage.CachedTokens > *usage.PromptTokens {
		return nil, "invalid"
	}
	if !found {
		return nil, "missing"
	}
	return usage, "valid"
}
