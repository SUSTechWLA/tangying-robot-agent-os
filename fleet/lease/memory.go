package lease

import (
	"context"
	"errors"
	"sync"
	"time"
)

type MemoryManager struct {
	mu     sync.Mutex
	now    func() time.Time
	grants map[string]Grant
	tokens map[string]uint64
}

func NewMemoryManager() *MemoryManager { return NewMemoryManagerWithClock(time.Now) }

func NewMemoryManagerWithClock(now func() time.Time) *MemoryManager {
	return &MemoryManager{now: now, grants: map[string]Grant{}, tokens: map[string]uint64{}}
}

func (m *MemoryManager) Acquire(_ context.Context, resourceID, owner string, ttl time.Duration) (Grant, error) {
	if err := validateRequest(resourceID, owner, ttl); err != nil {
		return Grant{}, err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	now := m.now().UTC()
	if grant, ok := m.grants[resourceID]; ok && now.Before(grant.ExpiresAt) {
		if grant.Owner == owner {
			grant.ExpiresAt = now.Add(ttl)
			m.grants[resourceID] = grant
			return grant, nil
		}
		return Grant{}, ErrLeaseHeld
	}
	m.tokens[resourceID]++
	grant := Grant{ResourceID: resourceID, Owner: owner, Token: m.tokens[resourceID], ExpiresAt: now.Add(ttl)}
	m.grants[resourceID] = grant
	return grant, nil
}

func (m *MemoryManager) Renew(_ context.Context, resourceID, owner string, token uint64, ttl time.Duration) (Grant, error) {
	if err := validateRequest(resourceID, owner, ttl); err != nil {
		return Grant{}, err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	grant, err := m.validateLocked(resourceID, owner, token)
	if err != nil {
		return Grant{}, err
	}
	grant.ExpiresAt = m.now().UTC().Add(ttl)
	m.grants[resourceID] = grant
	return grant, nil
}

func (m *MemoryManager) Transfer(_ context.Context, resourceID, fromOwner, toOwner string, token uint64, ttl time.Duration) (Grant, error) {
	if err := validateRequest(resourceID, fromOwner, ttl); err != nil || toOwner == "" {
		if err != nil {
			return Grant{}, err
		}
		return Grant{}, errors.New("new owner is required")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, err := m.validateLocked(resourceID, fromOwner, token); err != nil {
		return Grant{}, err
	}
	m.tokens[resourceID]++
	grant := Grant{ResourceID: resourceID, Owner: toOwner, Token: m.tokens[resourceID], ExpiresAt: m.now().UTC().Add(ttl)}
	m.grants[resourceID] = grant
	return grant, nil
}

func (m *MemoryManager) Validate(_ context.Context, resourceID, owner string, token uint64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	_, err := m.validateLocked(resourceID, owner, token)
	return err
}

func (m *MemoryManager) Release(_ context.Context, resourceID, owner string, token uint64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, err := m.validateLocked(resourceID, owner, token); err != nil {
		return err
	}
	delete(m.grants, resourceID)
	return nil
}

func (m *MemoryManager) validateLocked(resourceID, owner string, token uint64) (Grant, error) {
	grant, ok := m.grants[resourceID]
	if !ok {
		return Grant{}, ErrLeaseNotFound
	}
	if !m.now().UTC().Before(grant.ExpiresAt) {
		return Grant{}, ErrLeaseExpired
	}
	if grant.Owner != owner || grant.Token != token {
		return Grant{}, ErrStaleFencingToken
	}
	return grant, nil
}

func validateRequest(resourceID, owner string, ttl time.Duration) error {
	if resourceID == "" || owner == "" {
		return errors.New("resource id and owner are required")
	}
	if ttl <= 0 {
		return errors.New("lease ttl must be positive")
	}
	return nil
}
