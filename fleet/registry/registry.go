// Package registry tracks connected robot devices and their presence leases.
//
// A device is ONLINE while its lease has not expired; the lease is renewed by
// heartbeats arriving over the mTLS gRPC Link stream (or, in degraded mode,
// by the HTTP data plane). The registry is deliberately small: identity,
// capabilities and presence are all the control plane needs to route work.
package registry

import (
	"context"
	"errors"
	"sync"
	"time"
)

// Capability is a robot-side capability as reported at registration time.
type Capability struct {
	Name      string `json:"name"`
	Available bool   `json:"available"`
}

type ToolDescriptor struct {
	Name              string   `json:"name"`
	Description       string   `json:"description,omitempty"`
	DisplayName       string   `json:"displayName,omitempty"`
	Purpose           string   `json:"purpose,omitempty"`
	InputParameters   []string `json:"inputParameters,omitempty"`
	OutputParameters  []string `json:"outputParameters,omitempty"`
	SafeArgumentNames []string `json:"safeArgumentNames,omitempty"`
	SideEffectClass   string   `json:"sideEffectClass"`
	SafetyLevel       string   `json:"safetyLevel,omitempty"`
	Available         bool     `json:"available"`
}

type ObservationSource struct {
	SourceID          string   `json:"sourceId"`
	SourceType        string   `json:"sourceType"`
	SchemaRevision    string   `json:"schemaRevision,omitempty"`
	FrameIDs          []string `json:"frameIds,omitempty"`
	TransformRevision string   `json:"transformRevision"`
	UpdateRateHz      float64  `json:"updateRateHz,omitempty"`
	FreshnessBudgetMS uint32   `json:"freshnessBudgetMs"`
	PayloadKinds      []string `json:"payloadKinds,omitempty"`
	AdapterVersion    string   `json:"adapterVersion,omitempty"`
}

// Device is the control-plane view of one robot.
type Device struct {
	RobotID                    string              `json:"robotId"`
	Adapter                    string              `json:"adapter,omitempty"`
	SoftwareVersion            string              `json:"softwareVersion,omitempty"`
	ProtocolVersion            string              `json:"protocolVersion,omitempty"`
	RuntimeVersion             string              `json:"runtimeVersion,omitempty"`
	Capabilities               []Capability        `json:"capabilities,omitempty"`
	ToolCatalogRevision        string              `json:"toolCatalogRevision,omitempty"`
	ToolCatalog                []ToolDescriptor    `json:"toolCatalog,omitempty"`
	ObservationCatalogRevision string              `json:"observationCatalogRevision,omitempty"`
	ObservationSources         []ObservationSource `json:"observationSources,omitempty"`
	AdapterVersion             string              `json:"adapterVersion,omitempty"`
	Address                    string              `json:"address,omitempty"`
	LastSeen                   time.Time           `json:"lastSeen"`
	LeaseExpiry                time.Time           `json:"leaseExpiry"`
	Online                     bool                `json:"online"`
}

// Store persists device records. Implementations must be safe for concurrent
// use. Both memory and Redis stores are provided.
type Store interface {
	Upsert(context.Context, Device) error
	Get(context.Context, string) (Device, bool, error)
	List(context.Context) ([]Device, error)
	Remove(context.Context, string) error
}

// Registry is the device registry service facade.
type Registry struct {
	store Store
	now   func() time.Time
}

func New(store Store) *Registry { return NewWithClock(store, time.Now) }

func NewWithClock(store Store, now func() time.Time) *Registry {
	return &Registry{store: store, now: now}
}

// Register stores or refreshes the device record and grants a fresh lease.
func (r *Registry) Register(ctx context.Context, device Device, lease time.Duration) (time.Time, error) {
	now := r.now().UTC()
	device.LastSeen = now
	device.LeaseExpiry = now.Add(lease)
	device.Online = true
	if err := r.store.Upsert(ctx, device); err != nil {
		return time.Time{}, err
	}
	return device.LeaseExpiry, nil
}

// Heartbeat renews the device lease and returns the new expiry.
func (r *Registry) Heartbeat(ctx context.Context, robotID string, lease time.Duration) (time.Time, error) {
	device, ok, err := r.store.Get(ctx, robotID)
	if err != nil {
		return time.Time{}, err
	}
	now := r.now().UTC()
	if !ok {
		return time.Time{}, ErrDeviceNotRegistered
	}
	device.LastSeen = now
	device.LeaseExpiry = now.Add(lease)
	device.Online = true
	if err := r.store.Upsert(ctx, device); err != nil {
		return time.Time{}, err
	}
	return device.LeaseExpiry, nil
}

// Status returns the live device record with the Online flag recomputed
// against the current time.
func (r *Registry) Status(ctx context.Context, robotID string) (Device, bool, error) {
	device, ok, err := r.store.Get(ctx, robotID)
	if err != nil {
		return Device{}, false, err
	}
	if ok {
		device.Online = r.now().Before(device.LeaseExpiry)
	}
	return device, ok, nil
}

// List returns all devices with live Online flags.
func (r *Registry) List(ctx context.Context) ([]Device, error) {
	devices, err := r.store.List(ctx)
	if err != nil {
		return nil, err
	}
	now := r.now()
	for index := range devices {
		devices[index].Online = now.Before(devices[index].LeaseExpiry)
	}
	return devices, nil
}

// Remove forgets a device (used on explicit unregister).
func (r *Registry) Remove(ctx context.Context, robotID string) error {
	return r.store.Remove(ctx, robotID)
}

// MemoryStore is the in-process registry store used by tests and the
// no-Redis deployment profile.
type MemoryStore struct {
	mu      sync.RWMutex
	devices map[string]Device
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{devices: map[string]Device{}}
}

func (s *MemoryStore) Upsert(_ context.Context, device Device) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.devices[device.RobotID] = cloneDevice(device)
	return nil
}

func (s *MemoryStore) Get(_ context.Context, robotID string) (Device, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	device, ok := s.devices[robotID]
	return cloneDevice(device), ok, nil
}

func (s *MemoryStore) List(_ context.Context) ([]Device, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make([]Device, 0, len(s.devices))
	for _, device := range s.devices {
		result = append(result, cloneDevice(device))
	}
	return result, nil
}

func (s *MemoryStore) Remove(_ context.Context, robotID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.devices, robotID)
	return nil
}

// ErrMissingRobotID is returned when a heartbeat/status call lacks an id.
var ErrMissingRobotID = errors.New("robot id is required")

// ErrDeviceNotRegistered keeps heartbeats from implicitly creating an
// identity that never completed the authenticated registration handshake.
var ErrDeviceNotRegistered = errors.New("device is not registered")

func cloneDevice(device Device) Device {
	device.Capabilities = append([]Capability(nil), device.Capabilities...)
	device.ToolCatalog = append([]ToolDescriptor(nil), device.ToolCatalog...)
	for index := range device.ToolCatalog {
		device.ToolCatalog[index].InputParameters = append([]string(nil), device.ToolCatalog[index].InputParameters...)
		device.ToolCatalog[index].OutputParameters = append([]string(nil), device.ToolCatalog[index].OutputParameters...)
		device.ToolCatalog[index].SafeArgumentNames = append([]string(nil), device.ToolCatalog[index].SafeArgumentNames...)
	}
	device.ObservationSources = append([]ObservationSource(nil), device.ObservationSources...)
	for index := range device.ObservationSources {
		device.ObservationSources[index].FrameIDs = append([]string(nil), device.ObservationSources[index].FrameIDs...)
		device.ObservationSources[index].PayloadKinds = append([]string(nil), device.ObservationSources[index].PayloadKinds...)
	}
	return device
}
