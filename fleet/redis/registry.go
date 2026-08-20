package redis

import (
	"context"
	"encoding/json"

	"github.com/redis/go-redis/v9"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
)

// RegistryStore is a Redis-backed device registry store. Each device is a
// JSON document under fleet:device:{robot_id}; presence is derived from the
// lease expiry stored in the document, so no TTL juggling is needed and a
// stale record still shows as OFFLINE instead of disappearing.
type RegistryStore struct {
	client *redis.Client
}

func NewRegistryStore(addr, password string, db int) *RegistryStore {
	return &RegistryStore{client: redis.NewClient(&redis.Options{Addr: addr, Password: password, DB: db})}
}

func (s *RegistryStore) key(robotID string) string { return "fleet:device:" + robotID }

func (s *RegistryStore) Upsert(ctx context.Context, device registry.Device) error {
	data, err := json.Marshal(device)
	if err != nil {
		return err
	}
	return s.client.Set(ctx, s.key(device.RobotID), data, 0).Err()
}

func (s *RegistryStore) Get(ctx context.Context, robotID string) (registry.Device, bool, error) {
	data, err := s.client.Get(ctx, s.key(robotID)).Bytes()
	if err == redis.Nil {
		return registry.Device{}, false, nil
	}
	if err != nil {
		return registry.Device{}, false, err
	}
	var device registry.Device
	if err := json.Unmarshal(data, &device); err != nil {
		return registry.Device{}, false, err
	}
	return device, true, nil
}

func (s *RegistryStore) List(ctx context.Context) ([]registry.Device, error) {
	var devices []registry.Device
	iter := s.client.Scan(ctx, 0, "fleet:device:*", 100).Iterator()
	for iter.Next(ctx) {
		key := iter.Val()
		data, err := s.client.Get(ctx, key).Bytes()
		if err == redis.Nil {
			continue
		}
		if err != nil {
			return nil, err
		}
		var device registry.Device
		if err := json.Unmarshal(data, &device); err != nil {
			continue
		}
		devices = append(devices, device)
	}
	return devices, iter.Err()
}

func (s *RegistryStore) Remove(ctx context.Context, robotID string) error {
	return s.client.Del(ctx, s.key(robotID)).Err()
}

func (s *RegistryStore) Close() error { return s.client.Close() }
