package latency

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func sample(at time.Time, capability, outcome string, queue, admission, execute, verify time.Duration) Sample {
	return Sample{
		At: at, TaskID: "task-1", StepID: "step-" + capability, Capability: capability,
		SafetyLevel: "PHYSICAL", RobotID: "robot-1", Outcome: outcome,
		Queue: queue, Admission: admission, Execute: execute, Verify: verify,
	}
}

func fixed(samples ...Sample) *Recorder {
	recorder := New(DefaultCapacity)
	for _, item := range samples {
		recorder.Record(item)
	}
	return recorder
}

func TestReportSeparatesTheFourPhasesSoASlowSegmentIsNamed(t *testing.T) {
	now := time.Now()
	recorder := fixed(
		sample(now.Add(-3*time.Second), "navigation.navigate", OutcomeCompleted, 5*time.Millisecond, 2*time.Millisecond, 900*time.Millisecond, 3*time.Millisecond),
		sample(now.Add(-2*time.Second), "navigation.navigate", OutcomeCompleted, 6*time.Millisecond, 2*time.Millisecond, 1200*time.Millisecond, 4*time.Millisecond),
	)
	report, err := recorder.Report(GroupByCapability, time.Minute, now)
	if err != nil {
		t.Fatalf("report: %v", err)
	}
	if report.Count != 2 || len(report.Groups) != 1 {
		t.Fatalf("expected one group with two samples, got %#v", report)
	}
	group := report.Groups[0]
	if group.Key != "navigation.navigate" {
		t.Fatalf("group key = %q", group.Key)
	}
	// Nearest-rank percentiles: with two samples p50 is the lower one, never an
	// interpolated duration the robot did not actually take.
	if group.Phases[Execute].P50 != 900 || group.Phases[Execute].Max != 1200 {
		t.Fatalf("execute percentiles wrong: %#v", group.Phases[Execute])
	}
	if group.Phases[Execute].Mean != 1050 {
		t.Fatalf("execute mean = %v, want 1050", group.Phases[Execute].Mean)
	}
	if group.Phases[Queue].Max != 6 {
		t.Fatalf("queue max = %v, want 6 ms", group.Phases[Queue].Max)
	}
	if group.Total.P95 < 1210 {
		t.Fatalf("total p95 = %v, want the execute-dominated total", group.Total.P95)
	}
	if group.Outcomes[OutcomeCompleted] != 2 {
		t.Fatalf("outcomes = %#v", group.Outcomes)
	}
	if group.SlowestStepID == "" || group.SlowestTotalMS < 1210 {
		t.Fatalf("slowest step must be named: %#v", group)
	}
}

func TestFailuresAndUnknownOutcomesAreRecordedNotDiscarded(t *testing.T) {
	now := time.Now()
	recorder := fixed(
		sample(now, "manipulation.pick", OutcomeFailed, 0, 0, 40*time.Millisecond, time.Millisecond),
		sample(now, "manipulation.pick", OutcomeUnknown, 0, 0, 3*time.Second, 0),
		sample(now, "manipulation.pick", OutcomeCompleted, 0, 0, 200*time.Millisecond, time.Millisecond),
	)
	report, _ := recorder.Report(GroupByOutcome, 0, now)
	if len(report.Groups) != 3 {
		t.Fatalf("every outcome must be visible: %#v", report.Groups)
	}
	collected := map[string]float64{}
	for _, group := range report.Groups {
		collected[group.Key] = group.Phases[Execute].Max
	}
	if collected[OutcomeFailed] != 40 || collected[OutcomeUnknown] != 3000 {
		t.Fatalf("a failure or an unknown outcome lost its timing: %#v", collected)
	}
}

func TestTheWindowExcludesOldSamplesWithoutLosingTheRetainedCount(t *testing.T) {
	now := time.Now()
	recorder := fixed(
		sample(now.Add(-10*time.Minute), "navigation.navigate", OutcomeCompleted, 0, 0, time.Second, 0),
		sample(now.Add(-5*time.Second), "navigation.navigate", OutcomeCompleted, 0, 0, 2*time.Second, 0),
	)
	report, _ := recorder.Report(GroupByCapability, time.Minute, now)
	if report.Count != 1 {
		t.Fatalf("window count = %d, want 1", report.Count)
	}
	if report.RetainedSamples != 2 {
		t.Fatalf("retained = %d, want 2 (the window is a filter, not a deletion)", report.RetainedSamples)
	}
	everything, _ := recorder.Report(GroupByCapability, 0, now)
	if everything.Count != 2 {
		t.Fatalf("a zero window must mean everything retained, got %d", everything.Count)
	}
}

func TestTheRingIsBoundedAndReportsWhatItOverwrote(t *testing.T) {
	recorder := New(4)
	now := time.Now()
	for index := 0; index < 10; index++ {
		recorder.Record(sample(now.Add(time.Duration(index)*time.Second), "observe_scene",
			OutcomeCompleted, 0, 0, time.Duration(index)*time.Millisecond, 0))
	}
	report, _ := recorder.Report(GroupByCapability, 0, now.Add(20*time.Second))
	if report.RetainedSamples != 4 || report.Capacity != 4 {
		t.Fatalf("ring not bounded: %#v", report)
	}
	if report.DroppedSamples != 6 {
		t.Fatalf("dropped = %d, want 6 - silent truncation is what this field prevents",
			report.DroppedSamples)
	}
	// The survivors are the newest four.
	group := report.Groups[0]
	if group.Phases[Execute].Max != 9 {
		t.Fatalf("ring kept the wrong samples: %#v", group.Phases[Execute])
	}
}

func TestAnUnsupportedGroupingIsRefusedRatherThanDefaulted(t *testing.T) {
	_, err := fixed().Report(GroupBy("task"), time.Minute, time.Now())
	if err == nil {
		t.Fatal("an unknown grouping must be refused")
	}
	var unsupported *UnsupportedGroupingError
	if !asUnsupported(err, &unsupported) || !strings.Contains(err.Error(), "capability") {
		t.Fatalf("refusal must name what is supported: %v", err)
	}
}

func asUnsupported(err error, target **UnsupportedGroupingError) bool {
	typed, ok := err.(*UnsupportedGroupingError)
	if ok {
		*target = typed
	}
	return ok
}

func TestAnEmptyRecorderReportsZerosInsteadOfFailing(t *testing.T) {
	var nilRecorder *Recorder
	report, err := nilRecorder.Report(GroupByCapability, time.Minute, time.Now())
	if err != nil {
		t.Fatalf("a nil recorder must not fault a console read: %v", err)
	}
	if report.Count != 0 || report.Groups == nil {
		t.Fatalf("empty report must serialise as an empty list: %#v", report)
	}
	empty := New(0)
	if empty.capacity != DefaultCapacity {
		t.Fatalf("a non-positive capacity must fall back, got %d", empty.capacity)
	}
	nilRecorder.Record(sample(time.Now(), "x", OutcomeCompleted, 0, 0, 0, 0))
	if nilRecorder.Dropped() != 0 {
		t.Fatal("recording into a nil recorder must be a no-op")
	}
}

func TestTheReportIsSerialisableWithNamedPhases(t *testing.T) {
	recorder := fixed(sample(time.Now(), "navigation.navigate", OutcomeCompleted,
		5*time.Millisecond, time.Millisecond, 500*time.Millisecond, 2*time.Millisecond))
	report, _ := recorder.Report(GroupBySafety, time.Minute, time.Now())
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	for _, wanted := range []string{`"queue"`, `"admission"`, `"execute"`, `"verify"`, `"p95Ms"`, `"droppedSamples"`} {
		if !strings.Contains(string(encoded), wanted) {
			t.Fatalf("report JSON is missing %s: %s", wanted, encoded)
		}
	}
}

func TestSamplesWithNoTimestampGetOneAndDoNotBreakOrdering(t *testing.T) {
	recorder := New(2)
	recorder.Record(Sample{Capability: "observe_scene", Outcome: OutcomeCompleted})
	report, err := recorder.Report(GroupByCapability, 0, time.Now())
	if err != nil || report.Count != 1 {
		t.Fatalf("an unstamped sample must still be recorded: %#v %v", report, err)
	}
}

// ── cost ────────────────────────────────────────────────────────────────────
// Instrumentation is only acceptable if it is free at the scale it runs at. A
// step records once; a console read aggregates on demand. These benchmarks put
// numbers on both so the overhead claim in the upgrade document is measured
// rather than asserted.

func BenchmarkRecord(b *testing.B) {
	recorder := New(DefaultCapacity)
	now := time.Now()
	sample := sample(now, "navigation.navigate", OutcomeCompleted,
		5*time.Millisecond, time.Millisecond, 900*time.Millisecond, 2*time.Millisecond)
	b.ReportAllocs()
	for b.Loop() {
		recorder.Record(sample)
	}
}

func BenchmarkReportOneWindow(b *testing.B) {
	recorder := New(DefaultCapacity)
	now := time.Now()
	for index := 0; index < 500; index++ {
		recorder.Record(sample(now.Add(-time.Duration(index)*time.Second),
			"navigation.navigate", OutcomeCompleted, 5*time.Millisecond,
			time.Millisecond, 900*time.Millisecond, 2*time.Millisecond))
	}
	b.ReportAllocs()
	for b.Loop() {
		if _, err := recorder.Report(GroupByCapability, time.Hour, now); err != nil {
			b.Fatal(err)
		}
	}
}
