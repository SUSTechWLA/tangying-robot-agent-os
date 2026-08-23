package redis

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	redisv9 "github.com/redis/go-redis/v9"
)

var acquireLeaseScript = redisv9.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 1 then
  local owner = redis.call('HGET', KEYS[1], 'owner')
  if owner ~= ARGV[1] then return redis.error_reply('LEASE_HELD') end
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return tonumber(redis.call('HGET', KEYS[1], 'token'))
end
local token = redis.call('INCR', KEYS[2])
redis.call('HSET', KEYS[1], 'owner', ARGV[1], 'token', token)
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return token
`)

var renewLeaseScript = redisv9.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then return redis.error_reply('LEASE_NOT_FOUND') end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] or redis.call('HGET', KEYS[1], 'token') ~= ARGV[2] then
  return redis.error_reply('STALE_FENCING_TOKEN')
end
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return tonumber(ARGV[2])
`)

var transferLeaseScript = redisv9.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then return redis.error_reply('LEASE_NOT_FOUND') end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] or redis.call('HGET', KEYS[1], 'token') ~= ARGV[3] then
  return redis.error_reply('STALE_FENCING_TOKEN')
end
local token = redis.call('INCR', KEYS[2])
redis.call('HSET', KEYS[1], 'owner', ARGV[2], 'token', token)
redis.call('PEXPIRE', KEYS[1], ARGV[4])
return token
`)

var validateLeaseScript = redisv9.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then return redis.error_reply('LEASE_NOT_FOUND') end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] or redis.call('HGET', KEYS[1], 'token') ~= ARGV[2] then
  return redis.error_reply('STALE_FENCING_TOKEN')
end
return tonumber(ARGV[2])
`)

var releaseLeaseScript = redisv9.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 0 then return redis.error_reply('LEASE_NOT_FOUND') end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] or redis.call('HGET', KEYS[1], 'token') ~= ARGV[2] then
  return redis.error_reply('STALE_FENCING_TOKEN')
end
redis.call('DEL', KEYS[1])
return 1
`)

type LeaseManager struct {
	client *redisv9.Client
	prefix string
	now    func() time.Time
}

func NewLeaseManager(client *redisv9.Client, prefix string) *LeaseManager {
	prefix = strings.TrimSuffix(prefix, ":")
	if prefix == "" {
		prefix = "tangying:fleet"
	}
	return &LeaseManager{client: client, prefix: prefix, now: time.Now}
}

func (m *LeaseManager) Acquire(ctx context.Context, resourceID, owner string, ttl time.Duration) (lease.Grant, error) {
	if err := validateLeaseInput(resourceID, owner, ttl); err != nil {
		return lease.Grant{}, err
	}
	token, err := m.runToken(ctx, acquireLeaseScript, resourceID, owner, ttl.Milliseconds())
	if err != nil {
		return lease.Grant{}, err
	}
	return m.grant(resourceID, owner, token, ttl), nil
}

func (m *LeaseManager) Renew(ctx context.Context, resourceID, owner string, token uint64, ttl time.Duration) (lease.Grant, error) {
	if err := validateLeaseInput(resourceID, owner, ttl); err != nil {
		return lease.Grant{}, err
	}
	value, err := m.runToken(ctx, renewLeaseScript, resourceID, owner, strconv.FormatUint(token, 10), ttl.Milliseconds())
	if err != nil {
		return lease.Grant{}, err
	}
	return m.grant(resourceID, owner, value, ttl), nil
}

func (m *LeaseManager) Transfer(ctx context.Context, resourceID, fromOwner, toOwner string, token uint64, ttl time.Duration) (lease.Grant, error) {
	if err := validateLeaseInput(resourceID, fromOwner, ttl); err != nil {
		return lease.Grant{}, err
	}
	if toOwner == "" {
		return lease.Grant{}, errors.New("new owner is required")
	}
	value, err := m.runToken(ctx, transferLeaseScript, resourceID, fromOwner, toOwner, strconv.FormatUint(token, 10), ttl.Milliseconds())
	if err != nil {
		return lease.Grant{}, err
	}
	return m.grant(resourceID, toOwner, value, ttl), nil
}

func (m *LeaseManager) Validate(ctx context.Context, resourceID, owner string, token uint64) error {
	if resourceID == "" || owner == "" || token == 0 {
		return errors.New("resource id, owner and positive token are required")
	}
	_, err := m.runToken(ctx, validateLeaseScript, resourceID, owner, strconv.FormatUint(token, 10))
	return err
}

func (m *LeaseManager) Release(ctx context.Context, resourceID, owner string, token uint64) error {
	if resourceID == "" || owner == "" || token == 0 {
		return errors.New("resource id, owner and positive token are required")
	}
	_, err := m.runToken(ctx, releaseLeaseScript, resourceID, owner, strconv.FormatUint(token, 10))
	return err
}

func (m *LeaseManager) runToken(ctx context.Context, script *redisv9.Script, resourceID string, args ...any) (uint64, error) {
	if m.client == nil {
		return 0, errors.New("redis lease client is required")
	}
	stateKey, counterKey := m.keys(resourceID)
	value, err := script.Run(ctx, m.client, []string{stateKey, counterKey}, args...).Int64()
	if err != nil {
		return 0, mapLeaseError(err)
	}
	if value <= 0 {
		return 0, errors.New("redis returned an invalid fencing token")
	}
	return uint64(value), nil
}

func (m *LeaseManager) keys(resourceID string) (string, string) {
	sum := sha256.Sum256([]byte(resourceID))
	tag := hex.EncodeToString(sum[:8])
	base := m.prefix + ":lease:{" + tag + "}"
	return base + ":state", base + ":counter"
}

func (m *LeaseManager) grant(resourceID, owner string, token uint64, ttl time.Duration) lease.Grant {
	return lease.Grant{ResourceID: resourceID, Owner: owner, Token: token, ExpiresAt: m.now().UTC().Add(ttl)}
}

func validateLeaseInput(resourceID, owner string, ttl time.Duration) error {
	if resourceID == "" || owner == "" {
		return errors.New("resource id and owner are required")
	}
	if ttl <= 0 || ttl.Milliseconds() <= 0 {
		return errors.New("lease ttl must be positive")
	}
	return nil
}

func mapLeaseError(err error) error {
	message := err.Error()
	switch {
	case strings.Contains(message, "LEASE_HELD"):
		return lease.ErrLeaseHeld
	case strings.Contains(message, "LEASE_NOT_FOUND"):
		return lease.ErrLeaseNotFound
	case strings.Contains(message, "STALE_FENCING_TOKEN"):
		return lease.ErrStaleFencingToken
	default:
		return fmt.Errorf("redis lease mutation: %w", err)
	}
}

var _ lease.Manager = (*LeaseManager)(nil)
