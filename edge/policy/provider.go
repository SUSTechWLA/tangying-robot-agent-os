package policy

import (
	"context"
	"errors"
)

var (
	ErrProviderUnavailable = errors.New("policy provider is unavailable")
	ErrProviderTimeout     = errors.New("policy provider timed out")
	ErrResponseTooLarge    = errors.New("policy provider response is too large")
	ErrManifestDrift       = errors.New("policy manifest changed during inference")
)

// Provider supplies immutable policy metadata and one bounded decision. It
// has no authority to execute a command or mutate task/world state.
type Provider interface {
	Manifest(context.Context) (Manifest, error)
	Infer(context.Context, InferenceRequest) (Decision, error)
}
