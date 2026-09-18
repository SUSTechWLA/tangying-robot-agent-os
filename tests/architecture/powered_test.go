package architecture_test

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// The powered-capability audit.
//
// The most expensive defects in this repository were not missing features. They
// were features that were finished, commented, given an API, and never connected:
//
//   - `ANOMALY_STEP_LATENCY` has a complete rule and a severity, and no producer
//     ever filled the `Latency` facts it reads, so it could not fire once.
//   - `GET /v1/telemetry/latency` has a handler, percentiles and a bounded ring,
//     and no page ever requested it.
//   - `incidents.Bundle.Timing` documents itself as "the console's latency report
//     verbatim" and its only writer never set it, so it was null in 36 of 36
//     bundles.
//   - `observation.Causation` travels the Fleet wire — the serialiser reads it —
//     and nothing ever assigned it, so it was empty in every envelope.
//
// Nothing failed. Nothing was red. A finished thing with no input looks exactly
// like a finished thing that works, which is why this list is a test rather than
// a paragraph in a design doc.
//
// # How to read a failure
//
// Each entry declares whether the capability is powered, and the check proves the
// declaration against the source tree: a producer or consumer pattern that must
// match in non-test code, or must not. So the list cannot rot in either
// direction. Fixing an unpowered capability fails this test until the entry says
// so — which is the point, because "the list is empty" is the goal, and a silent
// fix would leave the next reader trusting a stale document.

type capability struct {
	// name is the capability, by the identifier a reader would grep for.
	name string
	// where names the file or area that owns it, so a failure is actionable.
	where string
	// powered is the declaration under test.
	powered bool
	// producer is a pattern that must appear in non-test Go source when the
	// capability has a data source.
	producer *regexp.Regexp
	// consumer is a pattern that must appear in web/ when something reads the
	// capability, for capabilities whose point is being read.
	consumer *regexp.Regexp
	// note explains why an unpowered entry is on the list, so the next reader
	// does not have to rediscover the reason.
	note string
	// scope, when set, limits the producer pattern to one file.
	//
	// It is needed because a field name is not unique across a repository: the
	// first version of this audit asked whether anything assigned `StepRuns:` and
	// got a yes from a telemetry source label that happens to share the name. A
	// grep-shaped check that does not say where it is looking reports confidence
	// it has not earned.
	scope string
}

var capabilities = []capability{
	{
		name:  "ops.anomaly_cleared",
		where: "agentruntime/opsagent.go",
		// Powered by the ledger work: a standing condition now has a closing edge,
		// which is what lets the record hold one row per episode.
		powered:  true,
		producer: regexp.MustCompile(`TopicOpsAnomalyCleared`),
	},
	{
		name:  "step reconciliation",
		where: "console/reconcile.go",
		// Powered: a person's conclusion is recorded, and it is what lifts the
		// block on proceeding.
		powered:  true,
		producer: regexp.MustCompile(`StepReconciliation\{`),
	},
	{
		name:  "evidence freshness verdict",
		where: "core/telemetry/freshness.go",
		// Powered: the verdict is computed from the source's declared budget
		// instead of being written as the literal "FRESH".
		powered:  true,
		producer: regexp.MustCompile(`EvidenceFreshness\(now\)`),
	},
	{
		name:    "ANOMALY_STEP_LATENCY",
		where:   "agentruntime/opsrules.go",
		powered: false,
		// The rule reads `ObservationInput.Latency`; no non-test file assigns it.
		// The latency recorder exists and measures four phases per step, so the
		// missing piece is a port from the recorder into the observer.
		producer: regexp.MustCompile(`(?m)^\s*Latency:\s*\[\]LatencyFact|input\.Latency\s*=`),
		note:     "规则完整、四段耗时也已采集，缺的是把 recorder 接进观察者的那个端口",
	},
	{
		name:     "telemetry latency report",
		where:    "console/server.go",
		powered:  false,
		consumer: regexp.MustCompile(`telemetry/latency`),
		note:     "handler 与 p50/p95/p99 都在，前端从不请求它",
	},
	{
		name:    "incident bundle timing",
		where:   "incidents/bundle.go (written by internal/localapp/app.go)",
		powered: false,
		// The field documents itself as "the console's latency report verbatim"
		// and the only writer never sets it.
		producer: regexp.MustCompile(`Timing:`),
		scope:    "internal/localapp/app.go",
		note:     "字段注释承诺的是控制台的延迟报告原文，唯一写入者从不设置它，36/36 为 null",
	},
	{
		name:    "observation causation",
		where:   "core/observation/envelope.go",
		powered: false,
		// The Fleet serialiser reads it onto the wire; no envelope builder assigns
		// it, so every value crossing that wire is empty. The telemetry Sample the
		// builders work from carries no task or command identity, which is why this
		// one is not a one-line fix.
		producer: regexp.MustCompile(`Causation:\s*observation\.Causation\{`),
		note:     "字段上线了、序列化器读它，但构造 envelope 的地方从不赋值；采样样本里没有任务身份，所以不是一行能补的",
	},
	{
		name:     "incident bundle steps and evidence",
		where:    "incidents/bundle.go (written by internal/localapp/app.go)",
		powered:  false,
		producer: regexp.MustCompile(`StepRuns:|Evidence:`),
		scope:    "internal/localapp/app.go",
		note:     "事故包准备的正是事后诊断要用的东西，而 36/36 的 stepRuns/evidence 都是空数组",
	},
}

func repoRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	return root
}

// TestEveryCapabilityIsAsPoweredAsItClaims is the audit.
//
// It reads the source tree the way a reader would: by grepping it. A capability
// declared powered must have its producer (or consumer) in non-test code; one
// declared unpowered must not. Either kind of drift fails, and the failure names
// the entry to update.
func TestEveryCapabilityIsAsPoweredAsItClaims(t *testing.T) {
	root := repoRoot(t)
	goSources := collectSources(t, root, func(path string, name string) bool {
		return strings.HasSuffix(name, ".go") && !strings.HasSuffix(name, "_test.go") &&
			!strings.HasPrefix(name, "test_") && !inVendor(path)
	})
	webSources := collectSources(t, root, func(path string, name string) bool {
		return strings.HasSuffix(name, ".js") && !strings.HasSuffix(name, "_test.mjs")
	})

	var unpowered []string
	for _, entry := range capabilities {
		produced := matchesIn(entry, goSources)
		consumed := matchesAny(entry.consumer, webSources)
		// A capability with a consumer pattern has to be read as well as written;
		// one without is judged by its producer alone.
		live := produced
		if entry.consumer != nil {
			live = produced || consumed
		}
		if live != entry.powered {
			t.Errorf("%s (%s): declared powered=%v, but %s.\n  producer matched: %v\n  consumer matched: %v\n  if this changed, update the entry — and if the capability is now powered, say so, because the goal is an empty list",
				entry.name, entry.where, entry.powered, describe(entry.powered), produced, consumed)
		}
		if !entry.powered {
			unpowered = append(unpowered, entry.name+" ("+entry.where+"): "+entry.note)
		}
	}
	sort.Strings(unpowered)
	t.Logf("capabilities with an implementation and no input, %d of %d:\n  - %s",
		len(unpowered), len(capabilities), strings.Join(unpowered, "\n  - "))
}

func describe(powered bool) string {
	if powered {
		return "no producer or consumer was found"
	}
	return "one was found, so it is powered now"
}

// matchesIn applies the entry's pattern, within its scope when it declares one.
func matchesIn(entry capability, sources map[string]string) bool {
	if entry.scope == "" {
		return matchesAny(entry.producer, sources)
	}
	for path, content := range sources {
		if !strings.HasSuffix(filepath.ToSlash(path), entry.scope) {
			continue
		}
		if entry.producer != nil && entry.producer.MatchString(content) {
			return true
		}
	}
	return false
}

func matchesAny(pattern *regexp.Regexp, sources map[string]string) bool {
	if pattern == nil {
		return false
	}
	for _, content := range sources {
		if pattern.MatchString(content) {
			return true
		}
	}
	return false
}

func collectSources(t *testing.T, root string, include func(path, name string) bool) map[string]string {
	t.Helper()
	sources := map[string]string{}
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			switch entry.Name() {
			case "node_modules", ".venv", "vendor", ".git", "artifacts", "__pycache__", ".gocache", ".gomodcache":
				return filepath.SkipDir
			}
			return nil
		}
		if !include(path, entry.Name()) {
			return nil
		}
		content, readErr := os.ReadFile(path)
		if readErr != nil {
			return nil
		}
		sources[path] = string(content)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return sources
}

func inVendor(path string) bool {
	return strings.Contains(path, string(filepath.Separator)+"vendor"+string(filepath.Separator))
}
