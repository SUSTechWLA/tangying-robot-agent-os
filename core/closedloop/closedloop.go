// Package closedloop defines the completion contract for tools that change the
// physical world.
//
// A tool result is a trigger to look at the world, never proof that the world
// changed as intended. This package therefore owns two decisions that must not
// be re-implemented per adapter:
//
//   - whether a declared physical write has produced enough fresh evidence to be
//     called complete (Gate), and
//   - what a failure means and therefore what is safe to do about it
//     (Classify + Class).
//
// Everything here is deterministic and free of I/O so the rules can be tested
// exhaustively, including the rule that an unknown physical outcome is never
// retried automatically.
//
// # What is deliberately not here
//
// There is no retry state machine. This package used to carry one — attempt
// budget, backoff, NextAttemptAt, an escalation ladder — and nothing ever drove
// it: `NewTrack` appeared only in this package's own tests. Meanwhile the failure
// classes it defined read as though bounded automatic retry were in effect, and
// the observer's advice for a transient failure said "retry per the existing
// policy", pointing at a policy that did not exist.
//
// It was removed rather than wired, for a reason worth recording: a retry budget
// for physical writes has to be durable to mean anything. An attempt counter held
// in memory resets when the process restarts, so an agent that crashed mid-retry
// would retry forever — the exact unbounded behaviour the ceiling was supposed to
// prevent. Making it real means persisting attempt counts, deciding who approves
// attempt two, and threading both through the evidence gate. That is a design,
// not a patch, and it must not be reintroduced from a test-only type.
//
// What actually happens is stated plainly instead: a physical failure ends the
// step, and re-attempting it is an operator decision, offered as
// `task.retry-step` in the recovery catalog where it requires approval.
package closedloop

import (
	"errors"
	"strings"
	"time"
)

// Class groups failure codes by the only recovery action that is safe for them.
type Class string

const (
	// Transient may be retried in place once the infrastructure recovers.
	Transient Class = "TRANSIENT"
	// Perception requires a new observation or search before acting again.
	Perception Class = "PERCEPTION"
	// Planning requires a new goal or plan; repeating the same command is
	// pointless.
	Planning Class = "PLANNING"
	// Permission means approval, profile, catalog or fencing state is wrong.
	Permission Class = "PERMISSION"
	// Resource means another owner holds the resource or a grant is missing.
	Resource Class = "RESOURCE"
	// Validation means the command itself was rejected as malformed. A retry
	// with identical arguments cannot succeed.
	Validation Class = "VALIDATION"
	// UnknownOutcome means the physical result of a dispatched action cannot be
	// determined. Automatic retry is forbidden.
	UnknownOutcome Class = "UNKNOWN_OUTCOME"
	// Fatal means the failure cannot be resolved by retrying the same work.
	Fatal Class = "FATAL"
)

// Errors a refused completion returns. They are the two answers Gate can give:
// the evidence is not there, or the evidence is there and does not describe this
// command.
var (
	ErrEvidenceRequired = errors.New("physical write requires fresh post-command evidence")
	ErrEvidenceStale    = errors.New("physical write evidence predates the dispatched command")
)

// classification is the ordered lookup table for failure codes. Order matters:
// the first matching rule wins, so more specific code sets are listed first.
// Codes are the ones actually emitted by the runtime, gateway and navigation
// bridge; an unrecognised code is classified UnknownOutcome rather than being
// guessed as retryable.
var classification = []struct {
	class Class
	codes map[string]struct{}
}{
	{UnknownOutcome, set(
		"EVIDENCE_INSUFFICIENT",
		"EXECUTION_OUTCOME_UNKNOWN", "PHYSICAL_OUTCOME_UNKNOWN", "RUNTIME_JOURNAL_UNAVAILABLE",
		"BACKEND_STOP_FAILED", "SERVICE_SHUTDOWN",
		// The action ran but the expected relation was never observed. This is
		// the genuinely unknown case, not a perception failure: the gripper
		// closed, or the place motion completed, and what it actually achieved
		// was not established. Treating it as perception would permit a retry
		// that repeats a physical action whose first attempt may have worked —
		// for a place, the object could already be sitting in the destination.
		//
		// These arrived by omission rather than by decision: the runtime passes
		// them as an argument to its relation check, so a scan for literal
		// failure returns missed the whole family.
		"PLACEMENT_NOT_OBSERVED", "GRASP_NOT_OBSERVED", "GRASP_NOT_DETECTED",
		"PLACEMENT_NOT_VERIFIED", "UNVERIFIED_WORLD_MUTATION",
		// A tool raised an unexpected exception. Nothing is known about the
		// arguments and nothing is known about whether the tool had already
		// reached the hardware before it threw, so the honest answer is the same
		// one an unrecognised code gets: treat the physical result as
		// undetermined and reconcile before acting again.
		//
		// It was filed under Validation, whose advice is "the arguments were
		// rejected, replaying them will not help" — a guess about a command
		// nothing had examined.
		"TOOL_EXECUTION_ERROR",
		// A real collision during the stow sweep. The arm was moving, contact
		// stopped it part-way, and its posture plus whatever it touched are not
		// established afterwards. Retrying a sweep that just collided could drive
		// the arm further into whatever it hit, so this is the unknown-outcome
		// case with the retry prohibition attached, not a perception failure.
		// Splitting it from the pre-flight prediction below is the fix: one code
		// covering "we simulated it and it would collide" and "it collided" gave
		// the classifier two physical meanings to choose between.
		"NAV_STOW_CONTACT",
		// The recovery classifier's own fallback: it could not classify the
		// failure. An unclassifiable failure is the definition of an unknown
		// outcome, so it lands here deliberately rather than by omission.
		"UNCLASSIFIED_EXECUTION_FAILURE",
		// The diagnosis layer's family for "the workflow failed and the specific
		// cause could not be established". Same reasoning as the fallback above.
		"WORKFLOW_FAULT",
	)},
	{Permission, set(
		"APPROVAL_REQUIRED", "SAFETY_PROFILE_REJECTED", "TOOL_CATALOG_REVISION_REQUIRED",
		"TOOL_CATALOG_STALE", "SKILL_NOT_ALLOWED", "CAPABILITY_UNAVAILABLE", "ROBOT_NOT_ARMED",
		"MOBILE_BASE_DISABLED", "EMERGENCY_STOP_LATCHED", "EMERGENCY_STOPPED", "RECOVERY_POLICY_REQUIRED",
		"CALIBRATION_REQUIRED", "ROBOT_PROFILE_INVALID",
		// The XLeRobot driver's own blocker codes. Every one is a commissioning or
		// configuration fact — a missing vendored source tree, an absent serial
		// device, an unparsable environment value — so repeating the command cannot
		// help and the operator has to install, configure or calibrate something.
		//
		// They are listed here because wiring the real robot's fault report made
		// them reach the agent for the first time. Unclassified, each would have
		// fallen through to the unknown-outcome default and told an operator to
		// reconcile the world over a missing directory.
		"UPSTREAM_NOT_FOUND", "SERIAL_PORTS_UNAVAILABLE",
		"XLEROBOT_LEROBOT_INTEGRATION_MISSING",
		"MAX_RELATIVE_TARGET_INVALID", "MAX_ACTION_CHUNK_LENGTH_INVALID",
		"XLEROBOT_MAX_RELATIVE_TARGET", "XLEROBOT_MAX_ACTION_CHUNK_LENGTH",
		// Refusals and unmet preconditions: repeating the command cannot help,
		// because what is missing is a decision, a calibration or a revision
		// rather than a retry. Grouped here by the same test the whole table
		// uses: the only safe next step is for a person or a higher layer to
		// change something, so no automatic retry is permitted.
		"DESTINATION_NOT_ALLOWED", "MAPPING_ACTIVE", "MOTION_STOPPING",
		"CALIBRATION_CHANGED", "REVISION_REQUIRED", "REVISION_CONFLICT",
		"RESOURCE_NOT_OWNED",
		// The policy layer refused the action. A retry cannot change that: the
		// model, map or calibration has to change first.
		"POLICY_COMPATIBILITY_OR_ACTION_REJECTED",
		// The robot is stopped. Resuming is a person's decision, not a retry:
		// software does not clear a safety stop.
		"SAFETY_STOPPED",
	)},
	{Resource, set(
		"RESOURCE_GRANT_REQUIRED", "FENCING_TOKEN_REQUIRED", "FENCING_TOKEN_STALE",
		"RESOURCE_OWNER_MISMATCH", "RESOURCE_LEASE_EXPIRED", "ROBOT_BUSY", "ROBOT_COMMISSIONING_ACTIVE",
		"IDEMPOTENCY_CONFLICT", "TARGET_REFERENCE_CONFLICT",
	)},
	{Perception, set(
		"GRASP_MISS", "GRASP_SLIP", "WRONG_OBJECT", "PLACE_UNSTABLE", "NAV_NOT_REACHED", "PERCEPTION_OCCLUDED",
		// GRASP_NOT_REACHED is the runtime's own code for "the end effector could
		// not get to the object within tolerance". It is produced whenever the
		// approach fails, so it is a known, explainable failure — not an unknown
		// physical outcome. Leaving it out of this table made the classifier fall
		// through to UnknownOutcome, which reports a known failure as
		// unrecoverable and forbids the retry that would actually work.
		"GRASP_NOT_REACHED",
		"OBJECT_NOT_FOUND", "DESTINATION_NOT_FOUND", "NAV_OBSERVATION_INVALID",
		"NAV_OBSERVATION_LOST", "NAV_POSE_INVALID", "NAV_LOCALIZATION_UNAVAILABLE",
		"NAV_PATH_OUT_OF_VIEW", "NAV_MAP_NOT_READY", "RECONSTRUCTION_INVALID",
		"ENTITY_PROVIDER_REQUIRED", "NAV_ARMS_STOWED",
		// The world is not as the plan assumed, or the robot cannot establish
		// where things are. The recovery is to look again — rescan, re-localize,
		// re-observe — not to repeat the motion. These are the codes a robot in
		// a home hits most often: furniture moved, a doorway blocked, a map gone
		// stale, an object no longer where it was remembered.
		//
		// GOAL_NOT_CLEAR and LOCALIZATION_NOT_CLEAR are preflight refusals from
		// the path planner: the base has not moved, so there is no unknown
		// physical outcome and forbidding a retry would be wrong.
		"GOAL_NOT_CLEAR", "LOCALIZATION_NOT_CLEAR", "LOCALIZATION_REQUIRED",
		"NO_KNOWN_PATH", "PATH_CLEARANCE_INSUFFICIENT", "STALE_CAPTURE",
		"CONTINUATION_FRAME_MISMATCH", "NAV_ARRIVAL_OBSERVATION_INVALID",
		"NAV_VELOCITY_INVALID", "NAV_GOAL_NOT_REACHED", "NAV_ODOM_GOAL_NOT_REACHED",
		"HELD_OBJECT_NOT_FOUND", "OBJECT_NOT_AVAILABLE", "TARGET_AMBIGUOUS",
		"NOT_HOLDING_OBJECT", "GRIPPER_OCCUPIED", "ARM_NOT_FOUND",
		"NAV_STOW_REQUIRED", "PRE_POSITION_NO_CLEAR_POSE",
		"PRE_POSITION_UNAVAILABLE", "RELEASE_CLEARANCE_NOT_REACHED",
		// Reviewed, and deliberately still here. GRASP_FAILED is returned after
		// the gripper closed, the arm lifted and the grasp check found nothing
		// held: the hand is known empty, the object may have been nudged, and
		// "re-observe then try again" is exactly right. PLACE_NOT_REACHED is
		// returned before the gripper opens, so nothing was released and the
		// object is still held — the effect provably did not happen. Both
		// contrast with their *_NOT_OBSERVED siblings, which are unknown
		// outcomes because the action ran and was never checked.
		"GRASP_FAILED", "PLACE_NOT_REACHED",
		// What the robot can currently see is not enough to act: an occluded or
		// unseen path, an obstacle it observed, an unknown drop, a grounding or
		// semantic resolution it could not make. The recovery is to look again.
		"NAV_OBSTACLE_OBSERVED", "NAV_PATH_OCCLUDED", "NAV_DEPTH_UNKNOWN",
		"DEPTH_STARVED", "GROUNDING_ABSENT", "SEMANTIC_AMBIGUOUS",
	)},
	{Planning, set(
		"CONTAINER_FULL",
		"TARGET_UNREACHABLE", "NAV_WORKSPACE_LIMIT", "NAV_DEADLINE_EXCEEDED",
		"NAV_STEP_LIMIT", "NAV_KINEMATICS_INVALID", "XLEROBOT_MAX_RELATIVE_TARGET",
		"XLEROBOT_MAX_ACTION_CHUNK_LENGTH", "POLICY_ACTION_CHUNK_REQUIRED",
		// The plan cannot be carried out as written: the goal is outside the
		// commissioned world, the route is bounded away, or the base is
		// geometrically unable to comply. Replaying the same goal repeats the
		// failure; a different goal or route is what is needed.
		"EMPTY_NAVIGATION_ROUTE", "NAV_ROUTE_LIMIT", "NAV_ROTATION_LIMIT",
		"NAV_ROTATION_POSITION_MISMATCH", "NAV_FORWARD_ONLY", "NAV_PLANAR_ONLY",
		"NAV_MODEL_COLLISION",
		// A collision predicted by simulating the whole sweep before moving
		// anything. Nothing is broken and no command was malformed; the path is
		// blocked, so the answer is a different plan rather than a repeat.
		"NAV_STOW_CONTACT_PREDICTED",
	)},
	{Validation, set(
		"CONTRACT_VIOLATION",
		"TOOL_PARAMETERS_INVALID", "COMMAND_PARAMETERS_INVALID", "SCHEMA_VERSION_UNSUPPORTED",
		"LEASE_REQUIRED", "LEASE_TOO_LONG", "IDEMPOTENCY_KEY_REQUIRED", "TASK_ID_REQUIRED",
		"COMMAND_ID_REQUIRED", "ROBOT_ID_REQUIRED", "ROBOT_ID_MISMATCH", "OBJECT_ID_REQUIRED",
		"WORLD_REVISION_REQUIRED", "ACTION_VALUE_OUT_OF_RANGE", "GRIPPER_VALUE_OUT_OF_RANGE",
		"ACTION_CHUNK_MALFORMED", "VERIFIER_INVALID_RESULT", "VERIFIER_REQUIRED",
		// Malformed or incomplete requests. A retry with identical arguments
		// cannot start working, so the advice must be to change the request.
		"INVALID_ARGUMENT", "INVALID_POSE", "DESTINATION_ID_REQUIRED",
		"REQUEST_ID_REQUIRED", "REQUEST_TOO_LARGE",
		// The stow pre-flight refusals. Each is returned after checking the sweep
		// and before anything moves: the gripper is not empty, the target is
		// outside the joint range, the current posture is outside the commissioned
		// trajectory, or the resulting geometry exceeds the commissioned
		// navigation envelope. Repeating the same command is refused identically,
		// which is this class's definition.
		//
		// They used to sit under Perception, where the advice is "re-observe and
		// try again" — an operator told to retry something that cannot succeed
		// until a gripper is emptied or the robot is re-commissioned.
		"NAV_STOW_REQUIRES_EMPTY_GRIPPERS", "NAV_STOW_JOINT_LIMIT",
		"NAV_STOW_START_UNSUPPORTED", "NAV_STOW_ENVELOPE_MISMATCH",
	)},
	{Transient, set(
		"NAV_VELOCITY_STALE", "NAV_COMMAND_STALE", "NAV_STATUS_TIMEOUT", "NAV_STATUS_INVALID",
		"NAV_BRIDGE_UNAVAILABLE", "COMMAND_LEASE_EXPIRED", "COMMAND_EXPIRED", "BACKEND_ERROR",
		// Infrastructure that is down or mid-transition. These are the cases
		// where repeating the command once the dependency recovers is correct.
		"SERVICE_UNAVAILABLE", "NAV_CONTROLLER_CLOSED", "MOTOR_LAYOUT_MISMATCH",
		"CAMERA_LAYOUT_MISMATCH", "CAMERA_PARENT_MISMATCH", "MAP_TOO_LARGE",
		"RECEIPT_CAPACITY", "REQUEST_IN_PROGRESS", "SCAN_NOT_READY", "SCAN_TOO_SMALL",
		"SURVEY_UNAVAILABLE", "CONTINUATION_UNAVAILABLE",
		// Transport and dependency failures. The connection or the provider is
		// down; once it is back, repeating the command is the right move.
		"CONNECTION_REFUSED", "EOF_LEASE_EXPIRED", "RPC_UNAVAILABLE",
		"RPC_DEADLINE_EXCEEDED", "TRANSPORT_ERROR", "RUNTIME_NOT_READY",
		"WORLD_NOT_READY", "EXECUTION_STORE_UNAVAILABLE", "EXECUTION_PORTS_MISSING",
		"POLICY_PROVIDER_TIMEOUT", "POLICY_PROVIDER_UNAVAILABLE",
		"POLICY_OBSERVATION_NOT_READY",
	)},
	{Fatal, set(
		"CANCELLED", "VERIFICATION_FAILED", "VERIFICATION_CONFIDENCE_LOW",
	)},
}

func set(codes ...string) map[string]struct{} {
	indexed := make(map[string]struct{}, len(codes))
	for _, code := range codes {
		indexed[code] = struct{}{}
	}
	return indexed
}

// Classify maps a runtime failure code to its only safe recovery action.
//
// An empty or unrecognised code is UnknownOutcome: the system knows something
// went wrong but not what reached the hardware, so no automatic retry is
// allowed.
func Classify(code string) Class {
	normalized := strings.ToUpper(strings.TrimSpace(code))
	if normalized == "" {
		return UnknownOutcome
	}
	for _, rule := range classification {
		if _, ok := rule.codes[normalized]; ok {
			return rule.class
		}
	}
	return UnknownOutcome
}

// Knows reports whether a code appears in the classification table.
//
// It exists because Classify cannot answer this. A code that IS listed as
// UnknownOutcome and a code that is not listed at all classify to the same value,
// and the difference matters: the first is a failure the system understands and
// has decided is unrecoverable, the second is one nobody has classified yet. A
// supervisor reporting the second as the first hides a missing table entry behind
// a safety-looking decision — which is exactly what happened with
// GRASP_NOT_REACHED before it was added.
func Knows(code string) bool {
	normalized := strings.ToUpper(strings.TrimSpace(code))
	if normalized == "" {
		return false
	}
	for _, rule := range classification {
		if _, ok := rule.codes[normalized]; ok {
			return true
		}
	}
	return false
}

// Retryable reports whether a retry would be permissible for this class of
// failure, in principle.
//
// It is a statement about the failure, not about the system: nothing retries
// automatically, and a physical re-attempt is an operator decision offered as
// `task.retry-step`. What this answers is the narrower question a reader of the
// table needs — "is this the kind of failure re-observing and trying again fixes,
// or is it one where the same attempt cannot succeed?" — and it is the assertion
// the classification tests are written against.
func (c Class) Retryable() bool {
	switch c {
	case Transient, Perception:
		return true
	default:
		return false
	}
}

// DispatchPrecision is the timestamp resolution robot runtimes report captures
// with. Runtime observations carry Unix milliseconds, so sub-millisecond
// ordering between a dispatch and the observation taken right after it is not
// observable in principle — not a tolerance we are granting. Both sides are
// therefore compared in whole milliseconds: an observation counted in the same
// millisecond as the dispatch or any later one can confirm the command, while
// anything counted in an earlier millisecond is refused.
const DispatchPrecision = time.Millisecond

// NormalizeDispatchTime is the single definition of "when did this command
// start", so the executor and the gate cannot disagree about whether a given
// observation post-dates the command.
func NormalizeDispatchTime(at time.Time) time.Time {
	if at.IsZero() {
		return at
	}
	return at.UTC().Truncate(DispatchPrecision)
}

// Evidence is one attachment offered as proof that the world changed.
type Evidence struct {
	// ObservationID identifies the record in the observation registry.
	ObservationID string
	// ObservedAt is the sensor acquisition time, not the receipt time.
	ObservedAt time.Time
	// Freshness is the projection's verdict for the declaring source.
	Freshness string
	// SourceID is the sensor or adapter that produced the observation.
	SourceID string
	// Confidence is the stored observation confidence.
	Confidence float64
}
