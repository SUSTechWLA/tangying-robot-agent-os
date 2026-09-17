// Package closedloop defines the completion contract for tools that change the
// physical world.
//
// A tool result is a trigger to look at the world, never proof that the world
// changed as intended. This package therefore owns two decisions that must not
// be re-implemented per adapter:
//
//   - whether a declared physical write has produced enough fresh evidence to
//     be called complete (Track), and
//   - what may be done next when it has not (Classify + Policy).
//
// Everything here is deterministic and free of I/O so the rules can be tested
// exhaustively, including the rule that an unknown physical outcome is never
// retried automatically.
package closedloop

import (
	"errors"
	"fmt"
	"strings"
	"time"
)

// State is the lifecycle of one physical write attempt.
type State string

const (
	// Pending means the command has not been dispatched yet.
	Pending State = "PENDING"
	// Executing means a dispatch is in flight; no verdict may be recorded.
	Executing State = "EXECUTING"
	// AwaitingEvidence means the tool reported success but no fresh post-command
	// observation has been attached. The physical outcome is not yet known.
	AwaitingEvidence State = "AWAITING_EVIDENCE"
	// Verified means fresh post-command evidence was attached and accepted.
	Verified State = "VERIFIED"
	// Retrying means a classified failure permits one more bounded attempt.
	Retrying State = "RETRYING"
	// Escalated means no automatic action remains; a human or a higher layer
	// must reconcile before anything else touches the hardware.
	Escalated State = "ESCALATED"
)

// Verdict is the outcome of one attempt, as reported by the tool runtime.
type Verdict string

const (
	// Succeeded means the tool returned success. It still needs evidence.
	Succeeded Verdict = "SUCCEEDED"
	// Failed means the tool returned a failure code.
	Failed Verdict = "FAILED"
	// Unknown means the transport or runtime could not report a terminal
	// outcome (timeout, disconnect, crash). It never implies failure or success.
	Unknown Verdict = "UNKNOWN"
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

// Errors returned by a Track when a transition is not permitted.
var (
	ErrEvidenceRequired = errors.New("physical write requires fresh post-command evidence")
	ErrEvidenceStale    = errors.New("physical write evidence predates the dispatched command")
	ErrTerminalState    = errors.New("closed loop attempt is already terminal")
	ErrUnknownRetry     = errors.New("unknown physical outcome must not be retried automatically")
	ErrRetriesExhausted = errors.New("closed loop retry budget exhausted")
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
		"NAV_STOW_REQUIRED", "NAV_STOW_CONTACT", "NAV_STOW_JOINT_LIMIT",
		"NAV_STOW_ENVELOPE_MISMATCH", "NAV_STOW_REQUIRES_EMPTY_GRIPPERS",
		"NAV_STOW_START_UNSUPPORTED", "PRE_POSITION_NO_CLEAR_POSE",
		"PRE_POSITION_UNAVAILABLE", "RELEASE_CLEARANCE_NOT_REACHED",
		"GRASP_FAILED", "PLACE_NOT_REACHED",
		// What the robot can currently see is not enough to act: an occluded or
		// unseen path, an obstacle it observed, an unknown drop, a grounding or
		// semantic resolution it could not make. The recovery is to look again.
		"NAV_OBSTACLE_OBSERVED", "NAV_PATH_OCCLUDED", "NAV_DEPTH_UNKNOWN",
		"DEPTH_STARVED", "GROUNDING_ABSENT", "SEMANTIC_AMBIGUOUS",
	)},
	{Planning, set(
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
	)},
	{Validation, set(
		"TOOL_PARAMETERS_INVALID", "COMMAND_PARAMETERS_INVALID", "SCHEMA_VERSION_UNSUPPORTED",
		"LEASE_REQUIRED", "LEASE_TOO_LONG", "IDEMPOTENCY_KEY_REQUIRED", "TASK_ID_REQUIRED",
		"COMMAND_ID_REQUIRED", "ROBOT_ID_REQUIRED", "ROBOT_ID_MISMATCH", "OBJECT_ID_REQUIRED",
		"WORLD_REVISION_REQUIRED", "ACTION_VALUE_OUT_OF_RANGE", "GRIPPER_VALUE_OUT_OF_RANGE",
		"ACTION_CHUNK_MALFORMED", "VERIFIER_INVALID_RESULT", "VERIFIER_REQUIRED",
		// Malformed or incomplete requests. A retry with identical arguments
		// cannot start working, so the advice must be to change the request.
		"INVALID_ARGUMENT", "INVALID_POSE", "DESTINATION_ID_REQUIRED",
		"REQUEST_ID_REQUIRED", "REQUEST_TOO_LARGE", "TOOL_EXECUTION_ERROR",
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

// Retryable reports whether the class permits another automatic attempt.
func (c Class) Retryable() bool {
	switch c {
	case Transient, Perception:
		return true
	default:
		return false
	}
}

// Backoff returns the deterministic delay before attempt number next (1-based).
// It is pure so callers can inject their own clock and tests can assert the
// schedule without sleeping.
func Backoff(next int, base, maximum time.Duration) time.Duration {
	if next <= 1 || base <= 0 {
		return 0
	}
	delay := base
	for attempt := 2; attempt < next; attempt++ {
		if delay >= maximum/2 {
			return maximum
		}
		delay *= 2
	}
	if maximum > 0 && delay > maximum {
		return maximum
	}
	return delay
}

// Policy bounds how many times one physical write may be attempted.
type Policy struct {
	// MaxAttempts is the total number of dispatches allowed including the
	// first. Must be at least 1.
	MaxAttempts int
	// BackoffBase and BackoffMax bound the delay between attempts.
	BackoffBase time.Duration
	BackoffMax  time.Duration
}

// DefaultPolicy is the conservative policy for reference workcell writes:
// one retry for transient and perception failures, no retry for anything that
// could have reached the hardware without a known result.
func DefaultPolicy() Policy {
	return Policy{MaxAttempts: 2, BackoffBase: 250 * time.Millisecond, BackoffMax: 2 * time.Second}
}

// Validate rejects a policy that could silently disable the retry ceiling.
func (p Policy) Validate() error {
	if p.MaxAttempts < 1 {
		return fmt.Errorf("closed loop policy requires at least one attempt, got %d", p.MaxAttempts)
	}
	if p.BackoffBase < 0 || p.BackoffMax < 0 {
		return errors.New("closed loop backoff must not be negative")
	}
	return nil
}

// Track is the single-attempt-per-dispatch state machine for one physical write.
//
// The zero value is not usable; construct it with NewTrack. A Track never
// reports Verified without evidence, and never authorises a retry after an
// unknown outcome.
type Track struct {
	policy       Policy
	stepID       string
	capability   string
	state        State
	attempts     int
	dispatchedAt time.Time
	class        Class
	code         string
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
// start", used by both the Agent and Track so they cannot disagree about
// whether a given observation post-dates the command.
func NormalizeDispatchTime(at time.Time) time.Time {
	if at.IsZero() {
		return at
	}
	return at.UTC().Truncate(DispatchPrecision)
}

// NewTrack starts tracking one physical write. dispatchedAt is the moment the
// command was handed to the runtime; evidence older than it cannot verify this
// attempt.
func NewTrack(stepID, capability string, dispatchedAt time.Time, policy Policy) (*Track, error) {
	if strings.TrimSpace(stepID) == "" {
		return nil, errors.New("closed loop requires a step id")
	}
	if strings.TrimSpace(capability) == "" {
		return nil, errors.New("closed loop requires a capability")
	}
	if policy.MaxAttempts == 0 {
		policy = DefaultPolicy()
	}
	if err := policy.Validate(); err != nil {
		return nil, err
	}
	return &Track{
		policy:       policy,
		stepID:       stepID,
		capability:   capability,
		state:        Pending,
		dispatchedAt: NormalizeDispatchTime(dispatchedAt),
	}, nil
}

func (t *Track) State() State        { return t.state }
func (t *Track) Attempts() int       { return t.attempts }
func (t *Track) Class() Class        { return t.class }
func (t *Track) FailureCode() string { return t.code }
func (t *Track) StepID() string      { return t.stepID }

// Dispatch records that the command has been handed to the runtime. It is the
// only transition that increases the attempt count.
func (t *Track) Dispatch(at time.Time) error {
	if t.state == Verified || t.state == Escalated {
		return fmt.Errorf("%w: %s", ErrTerminalState, t.state)
	}
	if t.attempts >= t.policy.MaxAttempts {
		return fmt.Errorf("%w: %d attempt(s)", ErrRetriesExhausted, t.attempts)
	}
	t.attempts++
	t.state = Executing
	t.dispatchedAt = NormalizeDispatchTime(at)
	t.class = ""
	t.code = ""
	return nil
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

// Fresh reports whether the evidence can verify a command dispatched at the
// track's current dispatch time. Freshness is evaluated against the recorded
// source verdict as well as the timestamps so a stale source cannot be
// laundered into a completion. It shares validateFreshness with Gate so both
// entry points cannot drift apart.
func (t *Track) Fresh(evidence Evidence) error {
	return validateFreshness(t.dispatchedAt, evidence)
}

// Record applies one attempt verdict and returns the next state.
//
// Success is only complete with fresh evidence. Failure is classified and
// either retried within budget or escalated. An unknown outcome always
// escalates: the caller must reconcile the world, not repeat the action.
func (t *Track) Record(verdict Verdict, code string, evidence *Evidence, now time.Time) (State, error) {
	if t.state == Verified || t.state == Escalated {
		return t.state, fmt.Errorf("%w: %s", ErrTerminalState, t.state)
	}
	if t.state != Executing {
		return t.state, fmt.Errorf("closed loop cannot record %s from state %s", verdict, t.state)
	}
	switch verdict {
	case Succeeded:
		if evidence == nil {
			t.state = AwaitingEvidence
			t.code = "EVIDENCE_REQUIRED"
			return t.state, ErrEvidenceRequired
		}
		if err := t.Fresh(*evidence); err != nil {
			t.state = AwaitingEvidence
			t.code = "EVIDENCE_STALE"
			return t.state, err
		}
		t.state = Verified
		t.code = ""
		return t.state, nil
	case Failed:
		t.class = Classify(code)
		t.code = code
		if !t.class.Retryable() {
			if t.class == UnknownOutcome {
				return t.escalate(), fmt.Errorf("%w: %s", ErrUnknownRetry, code)
			}
			return t.escalate(), nil
		}
		if t.attempts >= t.policy.MaxAttempts {
			return t.escalate(), fmt.Errorf("%w: %s after %d attempt(s)", ErrRetriesExhausted, code, t.attempts)
		}
		t.state = Retrying
		return t.state, nil
	case Unknown:
		t.class = UnknownOutcome
		t.code = code
		if t.code == "" {
			t.code = "EXECUTION_OUTCOME_UNKNOWN"
		}
		return t.escalate(), fmt.Errorf("%w: %s", ErrUnknownRetry, t.code)
	default:
		return t.state, fmt.Errorf("unknown closed loop verdict %q", verdict)
	}
}

func (t *Track) escalate() State {
	t.state = Escalated
	return t.state
}

// NextAttemptAt returns the earliest time a Retrying track may dispatch again.
// It is zero when the state is not Retrying.
func (t *Track) NextAttemptAt() time.Time {
	if t.state != Retrying {
		return time.Time{}
	}
	delay := Backoff(t.attempts+1, t.policy.BackoffBase, t.policy.BackoffMax)
	return t.dispatchedAt.Add(delay)
}

// RequiresReconciliation reports whether the tracked attempt ended in a state
// where the physical world must be inspected before further action.
func (t *Track) RequiresReconciliation() bool {
	return t.state == Escalated && t.class == UnknownOutcome
}

// Complete reports whether this track reached a verified completion.
func (t *Track) Complete() bool { return t.state == Verified }
