package modelroute

import (
	"errors"
	"strings"
)

// CachePolicy opts into a backend-specific hint. Off omits the hint; it cannot
// disable caching inside a remote provider. Namespace is a deployment label,
// never a credential or a substitute for server-side tenant authentication.
type CachePolicy struct {
	Mode      string `json:"mode,omitempty"`
	Namespace string `json:"namespace,omitempty"`
}

func (p CachePolicy) EffectiveMode() string {
	if p.Mode == "" {
		return "off"
	}
	return p.Mode
}

func (p CachePolicy) Validate() error {
	switch p.EffectiveMode() {
	case "off":
		if p.Namespace != "" {
			return errors.New("cache namespace requires an enabled cache mode")
		}
	case "openai", "vllm":
		if strings.TrimSpace(p.Namespace) == "" || len(p.Namespace) > 128 || strings.ContainsAny(p.Namespace, "\r\n\t") {
			return errors.New("cache namespace must be a nonempty deployment label of at most 128 bytes without control whitespace")
		}
	default:
		return errors.New("cache mode must be off, openai, or vllm")
	}
	return nil
}
