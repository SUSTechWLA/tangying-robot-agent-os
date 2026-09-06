package robotclient

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

type captureCursor struct {
	sequence   uint64
	timestamp  int64
	id, digest string
}

func digest(value any) string {
	wire, _ := json.Marshal(value)
	sum := sha256.Sum256(wire)
	return hex.EncodeToString(sum[:])
}
func (c *Client) acceptProfile(wire *robotv1.RuntimeInfo, snapshot *runtime.Snapshot) error {
	c.contractMu.Lock()
	defer c.contractMu.Unlock()
	if wire.RobotProfile == nil {
		if c.profileDigest != "" {
			return errors.New("robot profile disappeared; reconnect and revalidate adapter")
		}
		return nil
	}
	profile, err := robotcontract.DecodeProfile(wire.RobotProfile.AsMap())
	if err != nil {
		return fmt.Errorf("robot profile rejected: %w", err)
	}
	if profile.RobotID != wire.RobotId || profile.AdapterID != wire.Adapter || profile.AdapterVersion != wire.AdapterVersion {
		return errors.New("robot profile does not match runtime identity")
	}
	declared := map[string]bool{}
	for _, name := range profile.Tools {
		declared[name] = true
	}
	seen := map[string]bool{}
	for _, tool := range wire.Capabilities {
		if tool == nil || !declared[tool.Name] || seen[tool.Name] {
			return errors.New("runtime capabilities do not match robot profile")
		}
		seen[tool.Name] = true
		expectedSafety := "read_only"
		if robotcontract.PhysicalTool(tool.Name) || tool.Name == "emergency_stop" {
			expectedSafety = "physical_motion"
		}
		if tool.SafetyLevel != expectedSafety {
			return errors.New("robot tool safety class does not match canonical contract")
		}
	}
	if len(seen) != len(declared) {
		return errors.New("runtime capabilities omit profile tools")
	}
	hash := digest(profile)
	if c.profileDigest != "" && c.profileDigest != hash {
		return errors.New("robot profile changed; reconnect and revalidate adapter")
	}
	c.profileDigest = hash
	snapshot.RobotProfile = profile
	return nil
}

// Canonical reconstruction is authoritative. Legacy duplicate entity fields
// never override its validated coordinates, identities or semantic relations.
func (c *Client) acceptReconstruction(info runtime.Snapshot, wire *robotv1.Observation) (*robotcontract.Reconstruction, error) {
	if wire.WallTimeUnixMs <= 0 {
		return nil, errors.New("observation capture time is required")
	}
	if info.RobotProfile == nil {
		if wire.Reconstruction != nil {
			return nil, errors.New("reconstruction supplied without robot profile")
		}
		return nil, nil
	}
	if wire.Reconstruction == nil {
		return nil, errors.New("profile adapter omitted required reconstruction")
	}
	scene, err := robotcontract.DecodeReconstruction(wire.Reconstruction.AsMap())
	if err != nil {
		return nil, fmt.Errorf("reconstruction rejected: %w", err)
	}
	if err = scene.Validate(*info.RobotProfile, time.Now()); err != nil {
		return nil, fmt.Errorf("reconstruction rejected: %w", err)
	}
	if wire.ObservationId != scene.ObservationID || wire.WallTimeUnixMs != scene.ObservedAtUnixMS {
		return nil, errors.New("observation and reconstruction capture identities disagree")
	}
	c.contractMu.Lock()
	if c.captures == nil {
		c.captures = map[string]captureCursor{}
	}
	previous, seen := c.captures[scene.SourceID]
	hash := digest(scene)
	if seen && (scene.Sequence < previous.sequence || scene.ObservedAtUnixMS < previous.timestamp || (scene.Sequence == previous.sequence && hash != previous.digest) || (scene.Sequence > previous.sequence && scene.ObservationID == previous.id)) {
		c.contractMu.Unlock()
		return nil, errors.New("reconstruction replay or immutable capture changed")
	}
	c.captures[scene.SourceID] = captureCursor{sequence: scene.Sequence, timestamp: scene.ObservedAtUnixMS, id: scene.ObservationID, digest: hash}
	c.contractMu.Unlock()
	wire.Entities = nil
	for _, entity := range scene.Entities {
		wire.Entities = append(wire.Entities, &robotv1.SceneEntity{EntityId: entity.EntityID, Category: entity.Category, Attributes: entity.Attributes, PoseXyzQuat: entity.Pose, Confidence: entity.Confidence, Relation: entity.Relation})
	}
	return scene, nil
}
