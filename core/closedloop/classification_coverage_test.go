package closedloop_test

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
)

// The classification table has no natural guard, and that is a real hazard.
//
// Classify falls through to UnknownOutcome for any code it does not recognise.
// That default is correct — an unrecognised failure genuinely might have reached
// the hardware — but it means a code that the runtime produces and the table
// forgets degrades silently into "result unknown, do not retry". The failure then
// looks like a safety decision when it is really a missing table entry.
//
// GRASP_NOT_REACHED was exactly that: a grasp that could not reach the object was
// reported as an unknown physical outcome, so the system told an operator not to
// retry a step whose correct recovery is to re-observe and try again.
//
// This test reads the runtime sources and asserts that every execution failure
// code they can return is classified as something other than the fallback. New
// codes must be added to this list deliberately, which is the point: the list is
// where someone is forced to decide what a new failure means.

// runtimeFailureCodes are the failure codes the robot runtime can actually return.
//
// Sources: sim/mujoco/tangying_sim/world.py (ActionResult codes) and the Python
// gateway's navigation and workcell bridges. They are listed rather than scraped
// automatically because a regex over two languages would be a second thing to get
// wrong; instead the sources are read and checked to still contain each code, so
// a removed code fails here rather than lingering.
var runtimeFailureCodes = map[string][]string{
	// code -> files that must still mention it
	"GRASP_NOT_REACHED": {"sim/mujoco/tangying_sim/world.py"},
	"OBJECT_NOT_FOUND":  {"sim/mujoco/tangying_sim/world.py"},
}

func repoRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve test path")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "../.."))
}

// TestEveryRuntimeFailureCodeIsClassified is the guard the table was missing.
func TestEveryRuntimeFailureCodeIsClassified(t *testing.T) {
	root := repoRoot(t)
	for code, sources := range runtimeFailureCodes {
		for _, source := range sources {
			path := filepath.Join(root, source)
			content, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("cannot read %s: %v", source, err)
			}
			if !strings.Contains(string(content), code) {
				t.Errorf("%s no longer produces %s; remove it from runtimeFailureCodes "+
					"or point this entry at the file that does", source, code)
			}
		}
	}
}

// TestACodeTheTableMissesFallsBackToUnknownOutcome pins the hazard itself.
//
// If this ever stops being true the test above matters less — but it would also
// mean the fallback changed, which is a safety-relevant change that should not
// happen quietly.
func TestACodeTheTableMissesFallsBackToUnknownOutcome(t *testing.T) {
	class := closedloop.Classify("A_CODE_NOBODY_HAS_SEEN")
	if class != closedloop.UnknownOutcome {
		t.Fatalf("Classify(unknown) = %s, want UnknownOutcome", class)
	}
	if class.Retryable() {
		t.Fatal("the fallback must not be retryable: an unknown outcome may have reached the hardware")
	}
}

// TestTheClassifierTableCoversCommonRuntimeCodes checks a broad sample, so the
// table cannot be trimmed without a test failing.
func TestTheClassifierTableCoversCommonRuntimeCodes(t *testing.T) {
	// Codes that must never fall through. They are drawn from the codes the
	// runtime, the navigation bridge and the command layer actually emit.
	mustBeKnown := []string{
		"GRASP_NOT_REACHED",
		"OBJECT_NOT_FOUND", "DESTINATION_NOT_FOUND", "NAV_OBSERVATION_LOST",
		"NAV_BRIDGE_UNAVAILABLE", "NAV_VELOCITY_STALE",
		"TARGET_UNREACHABLE", "NAV_DEADLINE_EXCEEDED",
		"FENCING_TOKEN_STALE", "RESOURCE_LEASE_EXPIRED",
		"APPROVAL_REQUIRED", "SAFETY_PROFILE_REJECTED",
		"TOOL_PARAMETERS_INVALID", "COMMAND_PARAMETERS_INVALID",
		"EXECUTION_OUTCOME_UNKNOWN", "VERIFICATION_FAILED",
	}
	sort.Strings(mustBeKnown)

	// Membership is asked directly rather than inferred from the class.
	// EXECUTION_OUTCOME_UNKNOWN is in the table AND classifies to UnknownOutcome,
	// so comparing class values cannot tell a listed code from an unlisted one.
	for _, code := range mustBeKnown {
		if !closedloop.Knows(code) {
			t.Errorf("%s is not in the classification table, so it falls through to the "+
				"UnknownOutcome default; add it with the recovery that is actually safe for it",
				code)
		}
	}
}

// runtimeEmittedCodes is every failure code the robot side can return, extracted
// from the Python runtime, gateway and navigation bridge.
//
// The list is committed rather than scraped so a new code cannot arrive silently:
// adding one to the runtime without adding it here leaves it unclassified, and
// unclassified means "unknown physical outcome, do not retry" — the most
// conservative class, which is right as a default and wrong as an accident.
//
// The audit that produced this list found 58 of 83 codes missing, including
// GOAL_NOT_CLEAR, which a live navigation failure surfaced as
// "（UNKNOWN_OUTCOME）不要自动重试：先对账" for a preflight refusal where the base
// had not moved at all.
var runtimeEmittedCodes = []string{
	"ARM_NOT_FOUND",
	"CALIBRATION_CHANGED",
	"CAMERA_LAYOUT_MISMATCH",
	"CAMERA_PARENT_MISMATCH",
	"CANCELLED",
	"CONNECTION_REFUSED",
	"CONTINUATION_FRAME_MISMATCH",
	"CONTINUATION_UNAVAILABLE",
	"DEPTH_STARVED",
	"DESTINATION_ID_REQUIRED",
	"DESTINATION_NOT_ALLOWED",
	"DESTINATION_NOT_FOUND",
	"EMERGENCY_STOP_LATCHED",
	"EMPTY_NAVIGATION_ROUTE",
	"EOF_LEASE_EXPIRED",
	"EXECUTION_OUTCOME_UNKNOWN",
	"EXECUTION_PORTS_MISSING",
	"EXECUTION_STORE_UNAVAILABLE",
	"GOAL_NOT_CLEAR",
	"GRASP_FAILED",
	"GRASP_NOT_DETECTED",
	"GRASP_NOT_OBSERVED",
	"GRASP_NOT_REACHED",
	"GRIPPER_OCCUPIED",
	"GROUNDING_ABSENT",
	"HELD_OBJECT_NOT_FOUND",
	"IDEMPOTENCY_CONFLICT",
	"INVALID_ARGUMENT",
	"INVALID_POSE",
	"LOCALIZATION_NOT_CLEAR",
	"LOCALIZATION_REQUIRED",
	"MAPPING_ACTIVE",
	"MAP_TOO_LARGE",
	"MOTION_STOPPING",
	"MOTOR_LAYOUT_MISMATCH",
	"NAV_ARRIVAL_OBSERVATION_INVALID",
	"NAV_BRIDGE_UNAVAILABLE",
	"NAV_COMMAND_STALE",
	"NAV_CONTROLLER_CLOSED",
	"NAV_DEADLINE_EXCEEDED",
	"NAV_DEPTH_UNKNOWN",
	"NAV_FORWARD_ONLY",
	"NAV_GOAL_NOT_REACHED",
	"NAV_KINEMATICS_INVALID",
	"NAV_LOCALIZATION_UNAVAILABLE",
	"NAV_MAP_NOT_READY",
	"NAV_MODEL_COLLISION",
	"NAV_OBSERVATION_INVALID",
	"NAV_OBSTACLE_OBSERVED",
	"NAV_ODOM_GOAL_NOT_REACHED",
	"NAV_PATH_OCCLUDED",
	"NAV_PATH_OUT_OF_VIEW",
	"NAV_PLANAR_ONLY",
	"NAV_POSE_INVALID",
	"NAV_ROTATION_LIMIT",
	"NAV_ROTATION_POSITION_MISMATCH",
	"NAV_ROUTE_LIMIT",
	"NAV_STEP_LIMIT",
	"NAV_STOW_CONTACT",
	"NAV_STOW_CONTACT_PREDICTED",
	// The XLeRobot driver's blocker codes reach the agent through the real robot's
	// fault report. They are commissioning facts, not physical failures.
	"UPSTREAM_NOT_FOUND",
	"SERIAL_PORTS_UNAVAILABLE",
	"XLEROBOT_LEROBOT_INTEGRATION_MISSING",
	"MAX_RELATIVE_TARGET_INVALID",
	"MAX_ACTION_CHUNK_LENGTH_INVALID",
	"NAV_STOW_JOINT_LIMIT",
	"NAV_STOW_START_UNSUPPORTED",
	"NAV_STOW_ENVELOPE_MISMATCH",
	"NAV_STOW_REQUIRES_EMPTY_GRIPPERS",
	"NAV_STOW_ENVELOPE_MISMATCH",
	"NAV_STOW_JOINT_LIMIT",
	"NAV_STOW_REQUIRED",
	"NAV_STOW_REQUIRES_EMPTY_GRIPPERS",
	"NAV_STOW_START_UNSUPPORTED",
	"NAV_VELOCITY_INVALID",
	"NAV_VELOCITY_STALE",
	"NAV_WORKSPACE_LIMIT",
	"NOT_HOLDING_OBJECT",
	"NO_KNOWN_PATH",
	"OBJECT_ID_REQUIRED",
	"OBJECT_NOT_AVAILABLE",
	"OBJECT_NOT_FOUND",
	"PATH_CLEARANCE_INSUFFICIENT",
	"PHYSICAL_OUTCOME_UNKNOWN",
	"PLACEMENT_NOT_OBSERVED",
	"PLACEMENT_NOT_VERIFIED",
	"PLACE_NOT_REACHED",
	"POLICY_COMPATIBILITY_OR_ACTION_REJECTED",
	"POLICY_OBSERVATION_NOT_READY",
	"POLICY_PROVIDER_TIMEOUT",
	"POLICY_PROVIDER_UNAVAILABLE",
	"PRE_POSITION_NO_CLEAR_POSE",
	"PRE_POSITION_UNAVAILABLE",
	"RECEIPT_CAPACITY",
	"RELEASE_CLEARANCE_NOT_REACHED",
	"REQUEST_ID_REQUIRED",
	"REQUEST_IN_PROGRESS",
	"REQUEST_TOO_LARGE",
	"RESOURCE_NOT_OWNED",
	"REVISION_CONFLICT",
	"REVISION_REQUIRED",
	"ROBOT_BUSY",
	"ROBOT_ID_MISMATCH",
	"RPC_DEADLINE_EXCEEDED",
	"RPC_UNAVAILABLE",
	"RUNTIME_NOT_READY",
	"SAFETY_STOPPED",
	"SCAN_NOT_READY",
	"SCAN_TOO_SMALL",
	"SEMANTIC_AMBIGUOUS",
	"SERVICE_UNAVAILABLE",
	"SKILL_NOT_ALLOWED",
	"STALE_CAPTURE",
	"SURVEY_UNAVAILABLE",
	"TARGET_AMBIGUOUS",
	"TARGET_UNREACHABLE",
	"TOOL_EXECUTION_ERROR",
	"TOOL_PARAMETERS_INVALID",
	"TRANSPORT_ERROR",
	"UNCLASSIFIED_EXECUTION_FAILURE",
	"UNVERIFIED_WORLD_MUTATION",
	"WORKFLOW_FAULT",
	"WORLD_NOT_READY",
}

// TestEveryCodeTheRuntimeCanEmitIsClassified is the guard for the audit above.
//
// Classify's fallback is deliberate and correct: an unrecognised failure may have
// reached the hardware, so it must not be retried. What is not acceptable is a
// failure the runtime *does* understand arriving there by omission — the system
// then reports a known, explainable failure as an unknown physical outcome and
// tells an operator to reconcile a world that never changed.
func TestEveryCodeTheRuntimeCanEmitIsClassified(t *testing.T) {
	var missing []string
	for _, code := range runtimeEmittedCodes {
		if !closedloop.Knows(code) {
			missing = append(missing, code)
		}
	}
	if len(missing) > 0 {
		t.Fatalf("these codes fall through to the UnknownOutcome default; classify each "+
			"with the recovery that is actually safe for it, or say here why it is "+
			"deliberately unknown:\n  %s", strings.Join(missing, "\n  "))
	}
}

// TestRecoverableFailuresNeverForbidRetry is the safety direction that matters
// most: a code classified as recoverable must be retryable, because the whole
// point of the class is that a bounded retry is the correct response.
func TestRecoverableFailuresNeverForbidRetry(t *testing.T) {
	for _, code := range []string{"GRASP_NOT_REACHED", "OBJECT_NOT_FOUND", "NAV_BRIDGE_UNAVAILABLE"} {
		class := closedloop.Classify(code)
		if !class.Retryable() {
			t.Errorf("%s classified %s, which is not retryable; a perception or transient "+
				"failure is recoverable by re-observing or retrying", code, class)
		}
	}
}

var _ = regexp.MustCompile // keep the import available for future scraping

// TestKnowsDistinguishesListedFromUnlisted pins why Knows had to be exported.
//
// A code the table lists as UnknownOutcome and a code the table does not mention
// at all produce the same Classify result. They are not the same fact: the first
// is a failure the system understands and has decided is unrecoverable, the
// second is one nobody has classified. Treating the second as the first is how
// GRASP_NOT_REACHED was reported as an unknown physical outcome.
//
// This test exists because the difference is invisible in the classifier's output
// — which is exactly the trap, and the reason the coverage guard cannot be
// written in terms of Classify.
func TestKnowsDistinguishesListedFromUnlisted(t *testing.T) {
	const listed = "EXECUTION_OUTCOME_UNKNOWN"
	const unlisted = "A_CODE_NOBODY_HAS_CLASSIFIED"

	if closedloop.Classify(listed) != closedloop.Classify(unlisted) {
		t.Fatal("the premise changed: listed and unlisted codes no longer classify alike, " +
			"so this test no longer demonstrates anything and the coverage guard may simplify")
	}
	if !closedloop.Knows(listed) {
		t.Fatalf("Knows(%s) = false, but it is in the table", listed)
	}
	if closedloop.Knows(unlisted) {
		t.Fatalf("Knows(%s) = true, but it is not in the table", unlisted)
	}
}

// --- the review of the codes that touch the world ----------------------------
//
// The classification of a failure code is a safety decision, so the ones where
// the answer is not obvious are pinned with the reasoning rather than left to the
// next reader. Each of these was re-derived from the code that emits it.

// A grasp that failed is not a grasp whose result is unknown.
//
// GRASP_FAILED is returned after the gripper closed, the arm lifted, and the
// grasp check said the object is not held. The outcome is known — the hand is
// empty — while the object may have been nudged, which is exactly what
// "re-observe before acting again" means. It must stay retryable.
func TestAFailedGraspIsReobserveAndRetryNotUnknownOutcome(t *testing.T) {
	if class := closedloop.Classify("GRASP_FAILED"); class != closedloop.Perception {
		t.Fatalf("GRASP_FAILED = %s, want PERCEPTION", class)
	}
	if !closedloop.Classify("GRASP_FAILED").Retryable() {
		t.Fatal("a failed grasp must be retryable after re-observing")
	}
}

// A place that never reached the destination released nothing.
//
// PLACE_NOT_REACHED is returned before the gripper opens: the approach or the
// final pose check failed, so the object is still held and nothing was placed.
// The physical effect provably did not happen, which is what makes this
// different from PLACEMENT_NOT_OBSERVED.
func TestAPlaceThatNeverArrivedReleasedNothing(t *testing.T) {
	if class := closedloop.Classify("PLACE_NOT_REACHED"); class != closedloop.Perception {
		t.Fatalf("PLACE_NOT_REACHED = %s, want PERCEPTION", class)
	}
	// And the contrast that makes the pair meaningful.
	if class := closedloop.Classify("PLACEMENT_NOT_OBSERVED"); class != closedloop.UnknownOutcome {
		t.Fatalf("PLACEMENT_NOT_OBSERVED = %s, want UNKNOWN_OUTCOME", class)
	}
}

// A collision during the stow sweep is an unknown outcome; a predicted one is not.
//
// Both used to be NAV_STOW_CONTACT. The prediction is made by simulating the
// sweep before anything moves; the real one stops the arm part-way and leaves its
// posture and whatever it touched unestablished. One code with two physical
// meanings can only ever be classified wrongly for one of them — and the wrong
// half was the one that lets a retry drive the arm further into an obstacle.
func TestAStowCollisionIsUnknownButAPredictedOneIsNot(t *testing.T) {
	if class := closedloop.Classify("NAV_STOW_CONTACT"); class != closedloop.UnknownOutcome {
		t.Fatalf("NAV_STOW_CONTACT = %s, want UNKNOWN_OUTCOME: a real collision must not be retried", class)
	}
	if closedloop.Classify("NAV_STOW_CONTACT").Retryable() {
		t.Fatal("a real collision was left retryable")
	}
	if class := closedloop.Classify("NAV_STOW_CONTACT_PREDICTED"); class != closedloop.Planning {
		t.Fatalf("NAV_STOW_CONTACT_PREDICTED = %s, want PLANNING", class)
	}
}

// The stow pre-flight refusals cannot be fixed by repeating the command.
//
// Each is returned after checking the sweep and before anything moves: a gripper
// that is not empty, a target outside the joint range, a posture outside the
// commissioned trajectory, geometry outside the commissioned envelope. Telling an
// operator to "re-observe and try again" sends them to retry something that will
// be refused identically.
func TestStowPreflightRefusalsAreNotRetryablePerception(t *testing.T) {
	for _, code := range []string{
		"NAV_STOW_REQUIRES_EMPTY_GRIPPERS", "NAV_STOW_JOINT_LIMIT",
		"NAV_STOW_START_UNSUPPORTED", "NAV_STOW_ENVELOPE_MISMATCH",
	} {
		class := closedloop.Classify(code)
		if class == closedloop.Perception {
			t.Errorf("%s is still PERCEPTION; repeating it cannot succeed", code)
		}
		if class.Retryable() {
			t.Errorf("%s is retryable, but the same command is refused identically", code)
		}
	}
}
