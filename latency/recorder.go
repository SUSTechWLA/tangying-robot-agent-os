// Package latency keeps per-step execution timings so the system can answer
// "how long did that take, and which part was slow" about itself.
//
// The console used to derive durations from task CreatedAt/UpdatedAt, which is
// enough to show a number but not enough to optimize anything: it cannot say
// which phase of a step was slow, and it cannot survive the process that did the
// work. This records four phases per invocation - queue, admission, execute,
// verify - with the tool, safety class, robot and outcome that produced them.
//
// Design constraints, in order of importance:
//
//   - Bounded memory. A robot runs for weeks; a recorder that grows without
//     limit is a leak with extra steps. Samples live in a fixed ring, and the
//     number dropped by that ring is reported rather than hidden.
//   - Failures are data. A step that failed, or whose physical outcome is
//     unknown, is recorded with its outcome instead of being discarded: those
//     are the timings an operator actually wants.
//   - No external dependency and no background goroutine. Aggregation happens
//     on read, over a bounded sample set.
package latency

import (
	"sort"
	"sync"
	"time"
)

// Phase names one measured segment of a step. The split exists because the
// remedies differ: queueing means the scheduler is behind, admission means the
// safety gate is waiting, execute is the robot, and verify is evidence handling.
type Phase string

const (
	Queue     Phase = "queue"
	Admission Phase = "admission"
	Execute   Phase = "execute"
	Verify    Phase = "verify"
)

// Phases is the fixed order every report uses, so a reader never has to guess
// which segment a number belongs to.
var Phases = []Phase{Queue, Admission, Execute, Verify}

// Outcomes mirror the durable step statuses plus the two task-layer refusals
// that are not the hardware's fault. They are recorded verbatim.
const (
	OutcomeCompleted = "COMPLETED"
	OutcomeFailed    = "FAILED"
	// OutcomeUnknown means the runtime may have acted: the step is left STARTED
	// and its timing is still recorded, because "slow then unknown" is the most
	// important shape to be able to see.
	OutcomeUnknown = "UNKNOWN"
	// OutcomeUnverified means the command returned success but the closure gate
	// refused it. It is not a completion.
	OutcomeUnverified = "UNVERIFIED"
)

// Sample is one measured step invocation.
type Sample struct {
	At          time.Time
	TaskID      string
	StepID      string
	Capability  string
	SafetyLevel string
	RobotID     string
	Outcome     string
	Queue       time.Duration
	Admission   time.Duration
	Execute     time.Duration
	Verify      time.Duration
}

// Total is the wall time the four phases account for.
func (s Sample) Total() time.Duration {
	return s.Queue + s.Admission + s.Execute + s.Verify
}

// Duration returns one phase of the sample.
func (s Sample) Duration(phase Phase) time.Duration {
	switch phase {
	case Queue:
		return s.Queue
	case Admission:
		return s.Admission
	case Execute:
		return s.Execute
	case Verify:
		return s.Verify
	default:
		return 0
	}
}

// GroupBy selects how a report is broken down. Only these groupings are
// supported: a free-form key would let a caller slice the data until it says
// whatever they wanted, and unsupported keys fail loudly instead.
type GroupBy string

const (
	GroupByCapability GroupBy = "capability"
	GroupBySafety     GroupBy = "safety"
	GroupByRobot      GroupBy = "robot"
	GroupByOutcome    GroupBy = "outcome"
)

// DefaultCapacity is how many invocations are kept in memory. At a step every
// few seconds this is days of history for a household workload, and it is a few
// hundred kilobytes at most.
const DefaultCapacity = 4096

// Recorder is a bounded, concurrency-safe store of step timings.
type Recorder struct {
	mu       sync.Mutex
	samples  []Sample
	next     int
	filled   bool
	dropped  int
	capacity int
}

// New returns a recorder holding at most capacity samples. A non-positive
// capacity falls back to DefaultCapacity rather than creating a recorder that
// silently remembers nothing.
func New(capacity int) *Recorder {
	if capacity <= 0 {
		capacity = DefaultCapacity
	}
	return &Recorder{samples: make([]Sample, capacity), capacity: capacity}
}

// Record stores one sample. Unknown phases are not validated away: the phases
// are written by this package's callers, and a zero duration is a legitimate
// measurement (a read tool that needs no admission).
func (r *Recorder) Record(sample Sample) {
	if r == nil {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.samples[r.next] != (Sample{}) {
		r.dropped++
	}
	if sample.At.IsZero() {
		sample.At = time.Now()
	}
	r.samples[r.next] = sample
	r.next = (r.next + 1) % r.capacity
	if r.next == 0 {
		r.filled = true
	}
}

// Dropped reports how many samples the ring overwrote. It is reported next to
// every aggregate so a percentile is never mistaken for the whole history.
func (r *Recorder) Dropped() int {
	if r == nil {
		return 0
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.dropped
}

// snapshot copies the live samples in insertion order.
func (r *Recorder) snapshot() []Sample {
	r.mu.Lock()
	defer r.mu.Unlock()
	count := r.next
	if r.filled {
		count = r.capacity
	}
	result := make([]Sample, 0, count)
	for _, sample := range r.samples {
		if sample.At.IsZero() {
			continue
		}
		result = append(result, sample)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].At.Before(result[j].At) })
	return result
}

// Percentiles is the summary of one phase, in milliseconds.
type Percentiles struct {
	Count int     `json:"count"`
	P50   float64 `json:"p50Ms"`
	P95   float64 `json:"p95Ms"`
	P99   float64 `json:"p99Ms"`
	Max   float64 `json:"maxMs"`
	Mean  float64 `json:"meanMs"`
}

// Group is one breakdown row: a key, its step count, per-phase percentiles and
// the outcome tally that came with it.
type Group struct {
	Key      string                `json:"key"`
	Count    int                   `json:"count"`
	Outcomes map[string]int        `json:"outcomes"`
	Phases   map[Phase]Percentiles `json:"phases"`
	Total    Percentiles           `json:"total"`
	// Slowest names the step that contributed the largest total in this group.
	// An aggregate without an example is hard to act on.
	SlowestStepID  string  `json:"slowestStepId,omitempty"`
	SlowestTotalMS float64 `json:"slowestTotalMs,omitempty"`
}

// Report is one aggregation over a time window.
type Report struct {
	GroupBy   GroupBy `json:"groupBy"`
	WindowMS  int64   `json:"windowMs"`
	NowUnixMS int64   `json:"nowUnixMs"`
	// Count is how many samples fell inside the window; RetainedSamples is how
	// many the ring holds in total, and DroppedSamples how many it overwrote.
	// All three are reported so a reader can tell an empty window from a full one.
	Count           int     `json:"count"`
	RetainedSamples int     `json:"retainedSamples"`
	Capacity        int     `json:"capacity"`
	DroppedSamples  int     `json:"droppedSamples"`
	Groups          []Group `json:"groups"`
}

// Report aggregates the retained samples inside window, grouped by groupBy.
//
// A window of zero means "everything retained". An unsupported grouping returns
// an error instead of a default, because a metric that quietly answers a
// different question than the one asked is worse than no metric.
func (r *Recorder) Report(groupBy GroupBy, window time.Duration, now time.Time) (Report, error) {
	switch groupBy {
	case GroupByCapability, GroupBySafety, GroupByRobot, GroupByOutcome:
	default:
		return Report{}, &UnsupportedGroupingError{GroupBy: groupBy}
	}
	if now.IsZero() {
		now = time.Now()
	}
	report := Report{GroupBy: groupBy, WindowMS: window.Milliseconds(), NowUnixMS: now.UnixMilli()}
	if r == nil {
		report.Groups = []Group{}
		return report, nil
	}
	report.RetainedSamples = r.retained()
	report.Capacity = r.capacity
	report.DroppedSamples = r.Dropped()

	byKey := map[string][]Sample{}
	for _, sample := range r.snapshot() {
		if window > 0 && now.Sub(sample.At) > window {
			continue
		}
		byKey[groupKey(sample, groupBy)] = append(byKey[groupKey(sample, groupBy)], sample)
	}
	keys := make([]string, 0, len(byKey))
	for key := range byKey {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	report.Count = 0
	for _, key := range keys {
		samples := byKey[key]
		group := Group{Key: key, Count: len(samples), Outcomes: map[string]int{},
			Phases: map[Phase]Percentiles{}}
		report.Count += len(samples)
		totals := make([]time.Duration, 0, len(samples))
		slowest := time.Duration(-1)
		for _, sample := range samples {
			outcome := sample.Outcome
			if outcome == "" {
				outcome = "UNKNOWN_OUTCOME"
			}
			group.Outcomes[outcome]++
			total := sample.Total()
			totals = append(totals, total)
			if total > slowest {
				slowest, group.SlowestStepID = total, sample.StepID
			}
		}
		for _, phase := range Phases {
			values := make([]time.Duration, 0, len(samples))
			for _, sample := range samples {
				values = append(values, sample.Duration(phase))
			}
			group.Phases[phase] = summarise(values)
		}
		group.Total = summarise(totals)
		group.SlowestTotalMS = milliseconds(slowest)
		report.Groups = append(report.Groups, group)
	}
	if report.Groups == nil {
		report.Groups = []Group{}
	}
	return report, nil
}

func (r *Recorder) retained() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.filled {
		return r.capacity
	}
	return r.next
}

func groupKey(sample Sample, groupBy GroupBy) string {
	value := ""
	switch groupBy {
	case GroupByCapability:
		value = sample.Capability
	case GroupBySafety:
		value = sample.SafetyLevel
	case GroupByRobot:
		value = sample.RobotID
	case GroupByOutcome:
		value = sample.Outcome
	}
	if value == "" {
		return "unknown"
	}
	return value
}

// summarise computes percentiles over the values as given; callers pass one
// phase at a time. Nearest-rank percentiles are used deliberately: with a few
// dozen samples they are the honest answer, and they never interpolate a
// duration the robot did not actually take.
func summarise(values []time.Duration) Percentiles {
	if len(values) == 0 {
		return Percentiles{}
	}
	sorted := append([]time.Duration(nil), values...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	var sum time.Duration
	for _, value := range sorted {
		sum += value
	}
	return Percentiles{
		Count: len(sorted),
		P50:   milliseconds(rank(sorted, .50)),
		P95:   milliseconds(rank(sorted, .95)),
		P99:   milliseconds(rank(sorted, .99)),
		Max:   milliseconds(sorted[len(sorted)-1]),
		Mean:  milliseconds(sum / time.Duration(len(sorted))),
	}
}

func rank(sorted []time.Duration, quantile float64) time.Duration {
	if len(sorted) == 0 {
		return 0
	}
	index := int(quantile*float64(len(sorted))+0.999999) - 1
	if index < 0 {
		index = 0
	}
	if index >= len(sorted) {
		index = len(sorted) - 1
	}
	return sorted[index]
}

func milliseconds(value time.Duration) float64 {
	if value < 0 {
		return 0
	}
	return float64(value.Microseconds()) / 1000
}

// UnsupportedGroupingError is returned for a grouping this package does not
// define, so a caller sees a refusal rather than a metric about something else.
type UnsupportedGroupingError struct{ GroupBy GroupBy }

func (e *UnsupportedGroupingError) Error() string {
	return "unsupported latency grouping " + string(e.GroupBy) +
		" (supported: capability, safety, robot, outcome)"
}

// StepLatency adapts the recorder to the console's read contract, so the HTTP
// layer depends on an interface rather than on this concrete type.
func (r *Recorder) StepLatency(groupBy GroupBy, window time.Duration, now time.Time) (Report, error) {
	return r.Report(groupBy, window, now)
}
