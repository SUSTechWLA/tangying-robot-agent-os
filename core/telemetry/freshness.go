package telemetry

import (
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

// ClockSkewAllowance is how far ahead of the agent's clock a robot's observation
// may be dated before its time stops being usable to date anything.
//
// The agent and the robot are two clocks, and an embedded board without a synced
// time source drifts by seconds. A tolerance of a few hundred milliseconds —
// enough for two clocks on one machine — would turn ordinary skew into "the
// outcome is unknown" on every physical step, which is a worse failure than the
// one it guards against: a robot cannot meaningfully extend its own freshness
// budget by dating an observation forward, because it declares that budget too.
const ClockSkewAllowance = 5 * time.Second

// DefaultMaxAge is the longest freshness budget a profile may declare.
//
// It equals the ceiling core/robotcontract enforces on a sensor's MaxAgeMS, so
// no valid profile can claim an observation stays usable for longer than this.
const DefaultMaxAge = 60 * time.Second

// sourceBudget is how long an observation from sourceID stays usable.
//
// Three cases, and the conservative answer is not the same one in each:
//
//   - The source is named and the profile knows it: its own declared budget.
//   - The source is not identified — a snapshot with no reconstruction names no
//     sensor — or the profile does not know it. The strictest budget the profile
//     declares is used. A snapshot whose source cannot be established must not be
//     allowed to claim more freshness than the most demanding sensor the robot
//     has; the ceiling would let it claim more than any of them.
//   - There is no profile at all. The contract ceiling, which no valid profile may
//     exceed, so a deployment that simply sent no profile is not left with every
//     physical step refused.
func (s Snapshot) sourceBudget(sourceID string) time.Duration {
	if s.RobotProfile == nil {
		return DefaultMaxAge
	}
	if sourceID != "" {
		if sensor, ok := s.RobotProfile.Sensor(sourceID); ok && sensor.MaxAgeMS > 0 {
			return time.Duration(sensor.MaxAgeMS) * time.Millisecond
		}
	}
	strictest := time.Duration(0)
	for _, sensor := range s.RobotProfile.Sensors {
		if sensor.MaxAgeMS <= 0 {
			continue
		}
		budget := time.Duration(sensor.MaxAgeMS) * time.Millisecond
		if strictest == 0 || budget < strictest {
			strictest = budget
		}
	}
	if strictest == 0 {
		return DefaultMaxAge
	}
	return strictest
}

// EvidenceFreshness decides whether this snapshot is still within the budget its
// own source declared.
//
// The rule already existed and was already enforced — core/robotcontract rejects
// a reconstruction whose age exceeds its sensor's MaxAgeMS — but nothing applied
// it when a snapshot became closure evidence. Both callers, the task runner and
// the recovery executor, wrote the literal "FRESH" and downgraded it only for an
// emergency stop, so the gate's staleness rule could not fire for staleness at
// all. A verdict that is written rather than computed says whatever its writer
// assumed.
//
// # The budget
//
// A robot declares MaxAgeMS per sensor, capped by the profile contract at
// DefaultMaxAge. See sourceBudget for what is used when the source is not
// identified: the strictest budget the profile declares, or the ceiling when
// there is no profile to ask.
func (s Snapshot) EvidenceFreshness(now time.Time) worldmodel.Freshness {
	observedAt := s.ObservedAt
	sourceID := ""
	if s.Reconstruction != nil {
		if s.Reconstruction.ObservedAtUnixMS > 0 {
			observedAt = time.UnixMilli(s.Reconstruction.ObservedAtUnixMS)
		}
		sourceID = s.Reconstruction.SourceID
	}
	if observedAt.IsZero() {
		// Not FRESH. The gate treats both as unusable for closure, and between two
		// verdicts with the same consequence the honest one admits it does not know.
		return worldmodel.Unknown
	}
	budget := s.sourceBudget(sourceID)
	// Beyond the skew allowance the timestamp is not merely early, it is
	// unusable: an observation dated minutes into the future cannot say how old
	// the world it describes is.
	if now.Add(ClockSkewAllowance).Before(observedAt) {
		return worldmodel.Unknown
	}
	if now.Sub(observedAt) > budget {
		return worldmodel.Stale
	}
	return worldmodel.Fresh
}
