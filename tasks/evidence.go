package tasks

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image/png"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

const (
	MaxEvidenceCaptures      = 512
	MaxEvidenceImageBytes    = 2 * 1024 * 1024
	MaxEvidenceSnapshotBytes = 2 * 1024 * 1024
	MaxEvidenceRetainedBytes = 256 * 1024 * 1024
)

var (
	ErrEvidenceInvalid  = errors.New("invalid observation evidence")
	ErrEvidenceNotFound = errors.New("observation evidence not found")
	ErrEvidenceConflict = errors.New("observation evidence identity already has different content")
	ErrEvidenceCorrupt  = errors.New("observation evidence checksum mismatch")
)

// EvidenceRecord keeps historical capture identity even after raw-content
// retention expires. ID is task/step/capture scoped and safe in URL segments;
// CaptureID is the original sensor observation ID referenced by tool events.
type EvidenceRecord struct {
	SchemaVersion     string          `json:"schemaVersion"`
	ID                string          `json:"id"`
	RecordIndex       int64           `json:"recordIndex"`
	TaskID            string          `json:"taskId"`
	TaskRevision      uint64          `json:"taskRevision,omitempty"`
	StepID            string          `json:"stepId"`
	CaptureID         string          `json:"captureId"`
	RobotID           string          `json:"robotId"`
	Adapter           string          `json:"adapter"`
	SourceID          string          `json:"sourceId"`
	SourceType        string          `json:"sourceType"`
	SourceFrameID     string          `json:"sourceFrameId"`
	FrameID           string          `json:"frameId"`
	TransformRevision string          `json:"transformRevision"`
	CaptureSequence   uint64          `json:"captureSequence"`
	ObservedAtUnixMS  int64           `json:"observedAtUnixMs"`
	RecordedAt        time.Time       `json:"recordedAt"`
	Historical        bool            `json:"historical"`
	Expired           bool            `json:"expired"`
	ExpiredAt         *time.Time      `json:"expiredAt,omitempty"`
	SnapshotSHA256    string          `json:"snapshotSha256"`
	RGBSHA256         string          `json:"rgbSha256,omitempty"`
	DepthSHA256       string          `json:"depthSha256,omitempty"`
	SnapshotBytes     int             `json:"snapshotBytes"`
	RGBBytes          int             `json:"rgbBytes"`
	DepthBytes        int             `json:"depthBytes"`
	RGBMediaType      string          `json:"rgbMediaType,omitempty"`
	DepthMediaType    string          `json:"depthMediaType,omitempty"`
	Snapshot          json.RawMessage `json:"snapshot,omitempty"`
	RGB               []byte          `json:"-"`
	Depth             []byte          `json:"-"`
}

type EvidenceStore interface {
	RecordEvidence(context.Context, telemetry.Snapshot) (EvidenceRecord, error)
	ListEvidence(ctx context.Context, taskID string, limit int, beforeIndex int64) ([]EvidenceRecord, error)
	Evidence(ctx context.Context, taskID, evidenceID string) (EvidenceRecord, error)
}

func ValidEvidenceKey(value string) bool {
	if !utf8.ValidString(value) || utf8.RuneCountInString(value) == 0 || utf8.RuneCountInString(value) > 256 {
		return false
	}
	for _, char := range value {
		if unicode.IsSpace(char) || unicode.IsControl(char) {
			return false
		}
	}
	return true
}

// NewEvidence validates structure, source provenance and capture-time alignment.
// It deliberately does not apply a wall-clock freshness check: an archived
// capture must remain valid as historical evidence after its live lease ends.
func NewEvidence(snapshot telemetry.Snapshot, recordedAt time.Time) (EvidenceRecord, error) {
	invalid := func(reason string) (EvidenceRecord, error) {
		return EvidenceRecord{}, fmt.Errorf("%w: %s", ErrEvidenceInvalid, reason)
	}
	if !ValidEvidenceKey(snapshot.TaskID) || !ValidEvidenceKey(snapshot.StepID) || snapshot.Reconstruction == nil || snapshot.RobotProfile == nil {
		return invalid("task, step, robot profile and reconstruction are required")
	}
	capture := snapshot.Reconstruction
	if snapshot.SchemaVersion != "telemetry.v1" || snapshot.RobotID != capture.RobotID || snapshot.ObservedAt.IsZero() || snapshot.ObservedAt.UnixMilli() != capture.ObservedAtUnixMS {
		return invalid("snapshot robot identity or capture timestamp disagrees with reconstruction")
	}
	if !ValidEvidenceKey(capture.ObservationID) {
		return invalid("invalid capture identity")
	}
	if err := capture.Validate(*snapshot.RobotProfile, time.UnixMilli(capture.ObservedAtUnixMS)); err != nil {
		return invalid(err.Error())
	}
	if len(snapshot.Frame)+len(snapshot.DepthFrame) > MaxEvidenceImageBytes {
		return invalid("RGB plus depth exceeds 2 MiB")
	}
	var width, height int
	for _, media := range []struct {
		data []byte
		kind string
	}{{snapshot.Frame, snapshot.FrameMediaType}, {snapshot.DepthFrame, snapshot.DepthFrameMediaType}} {
		if len(media.data) == 0 {
			if media.kind != "" {
				return invalid("empty image cannot declare a media type")
			}
			continue
		}
		if media.kind != "image/png" {
			return invalid("historical RGB-D images must be PNG")
		}
		configuration, err := png.DecodeConfig(bytes.NewReader(media.data))
		if err != nil || configuration.Width < 1 || configuration.Width > 3840 || configuration.Height < 1 || configuration.Height > 2160 {
			return invalid("invalid or oversized PNG dimensions")
		}
		if width != 0 && (width != configuration.Width || height != configuration.Height) {
			return invalid("RGB and depth dimensions disagree")
		}
		width, height = configuration.Width, configuration.Height
		if _, err := png.Decode(bytes.NewReader(media.data)); err != nil {
			return invalid("invalid PNG image content")
		}
	}
	snapshot.ColorFrameAvailable, snapshot.DepthFrameAvailable = len(snapshot.Frame) > 0, len(snapshot.DepthFrame) > 0
	wire, err := json.Marshal(snapshot)
	if err != nil {
		return invalid(err.Error())
	}
	if len(wire) > MaxEvidenceSnapshotBytes {
		return invalid("snapshot exceeds 2 MiB")
	}
	identity, _ := json.Marshal([]string{snapshot.TaskID, snapshot.StepID, capture.ObservationID})
	return EvidenceRecord{
		SchemaVersion: "evidence.capture.v1", ID: EvidenceHash(identity), TaskID: snapshot.TaskID,
		TaskRevision: snapshot.TaskRevision, StepID: snapshot.StepID, CaptureID: capture.ObservationID,
		RobotID: snapshot.RobotID, Adapter: snapshot.Adapter, SourceID: capture.SourceID, SourceType: capture.SourceType,
		SourceFrameID: capture.SourceFrameID, FrameID: capture.FrameID, TransformRevision: capture.TransformRevision,
		CaptureSequence: capture.Sequence, ObservedAtUnixMS: capture.ObservedAtUnixMS, RecordedAt: recordedAt.UTC(), Historical: true,
		SnapshotSHA256: EvidenceHash(wire), RGBSHA256: EvidenceHash(snapshot.Frame), DepthSHA256: EvidenceHash(snapshot.DepthFrame),
		SnapshotBytes: len(wire), RGBBytes: len(snapshot.Frame), DepthBytes: len(snapshot.DepthFrame),
		RGBMediaType: snapshot.FrameMediaType, DepthMediaType: snapshot.DepthFrameMediaType,
		Snapshot: wire, RGB: append([]byte(nil), snapshot.Frame...), Depth: append([]byte(nil), snapshot.DepthFrame...),
	}, nil
}

func EvidenceHash(data []byte) string {
	if len(data) == 0 {
		return ""
	}
	hash := sha256.Sum256(data)
	return hex.EncodeToString(hash[:])
}
