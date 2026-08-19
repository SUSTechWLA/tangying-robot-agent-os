// Package redis provides Fleet middleware adapters backed by Redis.
package redis

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// Cache implements middleware.Cache with Redis. It is intentionally optional:
// correctness never depends on a cache hit.
type Cache struct {
	client *redis.Client
}

func NewCache(addr, password string, db int) *Cache {
	return &Cache{client: redis.NewClient(&redis.Options{
		Addr:     addr,
		Password: password,
		DB:       db,
	})}
}

func (c *Cache) Ping(ctx context.Context) error {
	return c.client.Ping(ctx).Err()
}

func (c *Cache) Get(ctx context.Context, key string) ([]byte, bool, error) {
	value, err := c.client.Get(ctx, key).Bytes()
	if err == redis.Nil {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	return value, true, nil
}

func (c *Cache) Set(ctx context.Context, key string, value []byte, ttl time.Duration) error {
	return c.client.Set(ctx, key, value, ttl).Err()
}

func (c *Cache) Delete(ctx context.Context, key string) error {
	return c.client.Del(ctx, key).Err()
}

func (c *Cache) Close() error {
	return c.client.Close()
}
