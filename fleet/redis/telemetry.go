package redis

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
)

const sensorCaptureTTL = 24 * time.Hour

var storeCaptureScript = redis.NewScript(`
if redis.call('EXISTS', KEYS[1]) == 1 then
  return {}
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[6])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[6])
redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[6])
redis.call('ZADD', KEYS[6], ARGV[5], ARGV[4])
redis.call('EXPIRE', KEYS[6], ARGV[6])
local current = tonumber(redis.call('GET', KEYS[5]) or '0')
if tonumber(ARGV[5]) > current then
  redis.call('SET', KEYS[4], ARGV[4], 'EX', ARGV[6])
  redis.call('SET', KEYS[5], ARGV[5], 'EX', ARGV[6])
end
if redis.call('EXISTS', KEYS[7]) == 1 then
  redis.call('EXPIRE', KEYS[7], ARGV[6])
end
local count = redis.call('ZCARD', KEYS[6])
if count <= 64 then
  return {}
end
local removed = redis.call('ZRANGE', KEYS[6], 0, count - 65)
if #removed > 0 then
  redis.call('ZREM', KEYS[6], unpack(removed))
end
return removed
`)

var correlateCaptureScript = redis.NewScript(`
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local revision = tonumber(ARGV[1])
if revision > current then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
end
return math.max(current, revision)
`)

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

func captureKeyPart(captureID string) string {
	return base64.RawURLEncoding.EncodeToString([]byte(captureID))
}

func (s *TelemetryStore) captureMetaKey(captureID string) string {
	return "fleet:sensors:capture:" + captureKeyPart(captureID) + ":meta"
}
func (s *TelemetryStore) captureFrameKey(captureID, modality string) string {
	return "fleet:sensors:capture:" + captureKeyPart(captureID) + ":" + modality
}
func (s *TelemetryStore) captureCorrelationKey(captureID string) string {
	return "fleet:sensors:capture:" + captureKeyPart(captureID) + ":world-revision"
}
func (s *TelemetryStore) latestCaptureKey(robotID string) string {
	return "fleet:sensors:latest:" + robotID
}
func (s *TelemetryStore) latestCaptureSequenceKey(robotID string) string {
	return "fleet:sensors:latest-sequence:" + robotID
}
func (s *TelemetryStore) captureIndexKey(robotID string) string {
	return "fleet:sensors:index:" + robotID
}

func (s *TelemetryStore) Ingest(ctx context.Context, sample telemetry.Sample) error {
	if sample.RobotID == "" {
		return errors.New("telemetry sample is missing robot id")
	}
	if sample.Capture != nil {
		capture := sample.Capture.Clone()
		if err := capture.Validate(); err != nil {
			return fmt.Errorf("invalid sensor capture: %w", err)
		}
		if capture.RobotID != sample.RobotID {
			return fmt.Errorf("sensor capture robot %q does not match sample robot %q", capture.RobotID, sample.RobotID)
		}
		if err := s.storeCapture(ctx, capture); err != nil {
			return err
		}
		stored, storedOK, err := s.Capture(ctx, capture.CaptureID)
		if err != nil {
			return err
		}
		if !storedOK || stored.RobotID != sample.RobotID {
			return fmt.Errorf("sensor capture id %q is owned by a different robot", capture.CaptureID)
		}
		latest, ok, err := s.LatestCapture(ctx, sample.RobotID)
		if err != nil {
			return err
		}
		if ok {
			sample.Capture = &latest
		} else {
			return errors.New("sensor capture was not retained for the sample robot")
		}
	}
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
	if sample.Capture != nil {
		capture, ok, captureErr := s.Capture(ctx, sample.Capture.CaptureID)
		if captureErr != nil {
			return telemetry.Sample{}, false, captureErr
		}
		if ok {
			sample.Capture = &capture
		}
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

func (s *TelemetryStore) storeCapture(ctx context.Context, capture *sensors.Capture) error {
	metadata := capture.Clone()
	var rgb, depth []byte
	for index := range metadata.Frames {
		switch metadata.Frames[index].Modality {
		case sensors.ModalityRGB:
			rgb = append([]byte(nil), metadata.Frames[index].Data...)
		case sensors.ModalityDepth:
			depth = append([]byte(nil), metadata.Frames[index].Data...)
		}
		metadata.Frames[index].Data = nil
	}
	encoded, err := json.Marshal(metadata)
	if err != nil {
		return err
	}
	ttlSeconds := strconv.FormatInt(int64(sensorCaptureTTL/time.Second), 10)
	removed, err := storeCaptureScript.Run(ctx, s.client, []string{
		s.captureMetaKey(capture.CaptureID),
		s.captureFrameKey(capture.CaptureID, sensors.ModalityRGB),
		s.captureFrameKey(capture.CaptureID, sensors.ModalityDepth),
		s.latestCaptureKey(capture.RobotID),
		s.latestCaptureSequenceKey(capture.RobotID),
		s.captureIndexKey(capture.RobotID),
		s.captureCorrelationKey(capture.CaptureID),
	}, encoded, rgb, depth, capture.CaptureID, capture.SourceSequence, ttlSeconds).StringSlice()
	if err != nil {
		return err
	}
	for _, captureID := range removed {
		if err := s.client.Del(ctx,
			s.captureMetaKey(captureID),
			s.captureFrameKey(captureID, sensors.ModalityRGB),
			s.captureFrameKey(captureID, sensors.ModalityDepth),
			s.captureCorrelationKey(captureID),
		).Err(); err != nil {
			return err
		}
	}
	return nil
}

func (s *TelemetryStore) LatestCapture(ctx context.Context, robotID string) (sensors.Capture, bool, error) {
	captureID, err := s.client.Get(ctx, s.latestCaptureKey(robotID)).Result()
	if err == redis.Nil {
		return sensors.Capture{}, false, nil
	}
	if err != nil {
		return sensors.Capture{}, false, err
	}
	return s.Capture(ctx, captureID)
}

func (s *TelemetryStore) Capture(ctx context.Context, captureID string) (sensors.Capture, bool, error) {
	data, err := s.client.Get(ctx, s.captureMetaKey(captureID)).Bytes()
	if err == redis.Nil {
		return sensors.Capture{}, false, nil
	}
	if err != nil {
		return sensors.Capture{}, false, err
	}
	var capture sensors.Capture
	if err := json.Unmarshal(data, &capture); err != nil {
		return sensors.Capture{}, false, err
	}
	frames, err := s.client.MGet(ctx,
		s.captureFrameKey(captureID, sensors.ModalityRGB),
		s.captureFrameKey(captureID, sensors.ModalityDepth),
	).Result()
	if err != nil {
		return sensors.Capture{}, false, err
	}
	frameBytes := map[string][]byte{}
	for index, modality := range []string{sensors.ModalityRGB, sensors.ModalityDepth} {
		if frames[index] == nil {
			return sensors.Capture{}, false, nil
		}
		value, ok := frames[index].(string)
		if !ok {
			return sensors.Capture{}, false, errors.New("sensor frame has an invalid Redis representation")
		}
		frameBytes[modality] = []byte(value)
	}
	for index := range capture.Frames {
		capture.Frames[index].Data = append([]byte(nil), frameBytes[capture.Frames[index].Modality]...)
	}
	if revision, revisionErr := s.client.Get(ctx, s.captureCorrelationKey(captureID)).Uint64(); revisionErr == nil && revision > capture.WorldRevision {
		capture.WorldRevision = revision
	} else if revisionErr != nil && revisionErr != redis.Nil {
		return sensors.Capture{}, false, revisionErr
	}
	if err := capture.Validate(); err != nil {
		return sensors.Capture{}, false, fmt.Errorf("stored sensor capture is invalid: %w", err)
	}
	return capture, true, nil
}

func (s *TelemetryStore) CorrelateCapture(ctx context.Context, captureID string, worldRevision uint64) error {
	if captureID == "" || worldRevision == 0 {
		return errors.New("capture id and positive world revision are required")
	}
	ttlSeconds := strconv.FormatInt(int64(sensorCaptureTTL/time.Second), 10)
	return correlateCaptureScript.Run(ctx, s.client,
		[]string{s.captureCorrelationKey(captureID)}, worldRevision, ttlSeconds,
	).Err()
}

func (s *TelemetryStore) Close() error { return s.client.Close() }

var _ telemetry.Store = (*TelemetryStore)(nil)
