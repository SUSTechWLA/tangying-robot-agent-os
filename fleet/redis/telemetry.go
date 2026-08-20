package redis

import (
	"context"
	"encoding/json"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
)

// TelemetryStore is a Redis-backed fleet telemetry store. The latest sample
// per robot lives in a plain key with a 24h TTL; the trajectory is a Redis
// Stream trimmed to a bounded length.
type TelemetryStore struct {
	client *redis.Client
}

func NewTelemetryStore(addr, password string, db int) *TelemetryStore {
	return &TelemetryStore{client: redis.NewClient(&redis.Options{Addr: addr, Password: password, DB: db})}
}

func (s *TelemetryStore) latestKey(robotID string) string { return "fleet:telemetry:latest:" + robotID }
func (s *TelemetryStore) trajKey(robotID string) string   { return "fleet:telemetry:traj:" + robotID }
func (s *TelemetryStore) frameKey(robotID string) string  { return "fleet:telemetry:frame:" + robotID }

func (s *TelemetryStore) Ingest(ctx context.Context, sample telemetry.Sample) error {
	data, err := json.Marshal(sample)
	if err != nil {
		return err
	}
	if err := s.client.Set(ctx, s.latestKey(sample.RobotID), data, 24*time.Hour).Err(); err != nil {
		return err
	}
	if len(sample.Frame) > 0 {
		mediaType := sample.FrameMediaType
		if mediaType == "" {
			mediaType = "image/png"
		}
		if err := s.client.Set(ctx, s.frameKey(sample.RobotID), sample.Frame, time.Hour).Err(); err != nil {
			return err
		}
		if err := s.client.Set(ctx, s.frameKey(sample.RobotID)+".type", mediaType, time.Hour).Err(); err != nil {
			return err
		}
	}
	_, err = s.client.XAdd(ctx, &redis.XAddArgs{
		Stream: s.trajKey(sample.RobotID),
		MaxLen: 600,
		Approx: true,
		Values: map[string]any{"sample": data},
	}).Result()
	return err
}

func (s *TelemetryStore) Latest(ctx context.Context, robotID string) (telemetry.Sample, bool, error) {
	data, err := s.client.Get(ctx, s.latestKey(robotID)).Bytes()
	if err == redis.Nil {
		return telemetry.Sample{}, false, nil
	}
	if err != nil {
		return telemetry.Sample{}, false, err
	}
	var sample telemetry.Sample
	if err := json.Unmarshal(data, &sample); err != nil {
		return telemetry.Sample{}, false, err
	}
	return sample, true, nil
}

func (s *TelemetryStore) Trajectory(ctx context.Context, robotID string, limit int) ([]telemetry.Sample, error) {
	result, err := s.client.XRevRangeN(ctx, s.trajKey(robotID), "+", "-", int64(limit)).Result()
	if err != nil {
		return nil, err
	}
	samples := make([]telemetry.Sample, 0, len(result))
	for index := len(result) - 1; index >= 0; index-- {
		message := result[index]
		raw, ok := message.Values["sample"].(string)
		if !ok {
			continue
		}
		var sample telemetry.Sample
		if err := json.Unmarshal([]byte(raw), &sample); err != nil {
			continue
		}
		samples = append(samples, sample)
	}
	return samples, nil
}

func (s *TelemetryStore) Frame(ctx context.Context, robotID string) (telemetry.Frame, bool, error) {
	data, err := s.client.Get(ctx, s.frameKey(robotID)).Bytes()
	if err == redis.Nil {
		return telemetry.Frame{}, false, nil
	}
	if err != nil {
		return telemetry.Frame{}, false, err
	}
	mediaType, err := s.client.Get(ctx, s.frameKey(robotID)+".type").Result()
	if err != nil {
		mediaType = "image/png"
	}
	return telemetry.Frame{Data: data, MediaType: mediaType}, true, nil
}

func (s *TelemetryStore) Robots(ctx context.Context) ([]string, error) {
	var robots []string
	iter := s.client.Scan(ctx, 0, "fleet:telemetry:latest:*", 100).Iterator()
	for iter.Next(ctx) {
		key := iter.Val()
		robots = append(robots, key[len("fleet:telemetry:latest:"):])
	}
	return robots, iter.Err()
}

func (s *TelemetryStore) Close() error { return s.client.Close() }
