// Command nl-eval scores the natural-language path against a case set.
//
// It answers one question: given a request, does this configuration produce a plan
// that would run, and does it refuse the requests that must not run? Run it against
// the deterministic baseline, against a hosted model, and against a locally served
// fine-tune, then compare the reports. That comparison is the acceptance gate for a
// post-training run — a checkpoint is promoted because its numbers are better on
// this set, not because its samples read well.
//
// Usage:
//
//	nl-eval                                    # deterministic baseline
//	nl-eval -provider openai -base-url URL -api-key K -model M
//	nl-eval -json report.json                  # machine-readable, for a training loop
//
// Exit status is 0 when every case passed, 1 when any case failed, and 2 when the
// run itself could not be completed. The distinction matters to a training loop:
// a failing score is a signal, an unreachable endpoint is not.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration/eval"
)

func main() {
	var (
		provider = flag.String("provider", "deterministic", "agent provider: deterministic or openai")
		baseURL  = flag.String("base-url", os.Getenv("AGENT_BASE_URL"), "OpenAI-compatible base URL")
		apiKey   = flag.String("api-key", os.Getenv("AGENT_API_KEY"), "OpenAI-compatible API key")
		model    = flag.String("model", os.Getenv("AGENT_MODEL"), "model name")
		samples  = flag.Int("samples", 1, "orchestration samples for consensus")
		label    = flag.String("label", "", "name for this run; defaults to the model or provider")
		jsonOut  = flag.String("json", "", "write the full report here as JSON")
		verbose  = flag.Bool("v", false, "print every case, not just the failures")
	)
	flag.Parse()

	name := *label
	if name == "" {
		if *model != "" {
			name = *model
		} else {
			name = *provider
		}
	}

	stack := eval.NewStack(name,
		agent.Config{Provider: *provider, BaseURL: *baseURL, APIKey: *apiKey, Model: *model},
		orchestration.Config{
			Provider: *provider, BaseURL: *baseURL, APIKey: *apiKey, Model: *model, Samples: *samples,
		})

	report := eval.Run(stack, eval.DefaultCases)
	fmt.Print(report.Summary())
	if *verbose {
		for _, outcome := range report.Outcomes {
			mark := "✓"
			if !outcome.Passed {
				mark = "✗"
			}
			fmt.Printf("  %s %-26s refused=%-5v skills=%v\n",
				mark, outcome.CaseID, outcome.Refused, outcome.Skills)
		}
	}

	if *jsonOut != "" {
		encoded, err := json.MarshalIndent(report, "", "  ")
		if err != nil {
			fmt.Fprintf(os.Stderr, "encode report: %v\n", err)
			os.Exit(2)
		}
		if err := os.WriteFile(*jsonOut, append(encoded, '\n'), 0o644); err != nil {
			fmt.Fprintf(os.Stderr, "write report: %v\n", err)
			os.Exit(2)
		}
		fmt.Printf("report written to %s\n", *jsonOut)
	}

	// A run where nothing could be answered at all is a broken harness or a broken
	// endpoint, not a bad score. Saying so with a different exit code keeps a
	// training loop from discarding a checkpoint over a dropped connection.
	if report.Passed == 0 && allErrored(report) {
		fmt.Fprintln(os.Stderr, "every case failed to run; the endpoint is probably unreachable")
		os.Exit(2)
	}
	if report.Passed != report.Cases {
		os.Exit(1)
	}
}

func allErrored(report eval.Report) bool {
	for _, outcome := range report.Outcomes {
		if outcome.Error == "" {
			return false
		}
	}
	return len(report.Outcomes) > 0
}
