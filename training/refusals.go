package training

import (
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// Parser turns a request into an intent, or refuses it. `agent.Parser` satisfies
// this, and so does the deterministic parser alone.
type Parser interface {
	Parse(request string) (manipulation.Intent, error)
}

// Refusal is one request the system declined, with the sentence it declined with.
type Refusal struct {
	Request string `json:"request"`
	Reason  string `json:"reason"`
}

// HarvestRefusals turns a corpus of requests into refusal samples.
//
// # Why this cannot come from the ledger
//
// A refused request never becomes a task. The service rejects it at creation and
// stores nothing, so the archive contains only the requests that were accepted —
// which is precisely the wrong sample. A training set assembled from the ledger is
// a training set of requests the system already agreed to do.
//
// That is the most dangerous way to build this dataset. A fine-tune taught only
// from accepted requests learns that every request has an answer, and "know when to
// decline" is the ability distillation loses first and needs most. So refusals are
// *harvested* — by running a parser over a corpus that includes the awkward cases —
// rather than read from a table that cannot contain them.
//
// # The corpus, and the gap in it
//
// The starting corpus is the refusal half of `orchestration/eval.DefaultCases`:
// the negations, the conditionals and the requests for capabilities this robot does
// not have. A deployment should extend it with the requests its own users actually
// typed and had rejected — which requires logging rejections, which this repository
// does not do yet.
//
// That gap is stated rather than papered over: **today's refusal set is as large as
// somebody bothered to write down, not as large as the system has seen.** Closing
// it is a small change (append rejected requests to the ledger) and it is the
// highest-value data collection available, because refusals are the samples that
// cannot be recovered any other way.
func HarvestRefusals(parser Parser, requests []string) []Refusal {
	refusals := make([]Refusal, 0, len(requests))
	for _, request := range requests {
		if strings.TrimSpace(request) == "" {
			continue
		}
		if _, err := parser.Parse(request); err != nil {
			refusals = append(refusals, Refusal{Request: request, Reason: err.Error()})
		}
	}
	return refusals
}

// Accepted returns the requests a parser can plan, which is the complement of
// HarvestRefusals.
//
// It is here so a corpus can be split into both halves in one pass and the split
// can be asserted: a corpus where everything is refused yields no positives, and
// one where nothing is refused yields no refusals. Either would train a degenerate
// model, and both are worth failing loudly on.
func Accepted(parser Parser, requests []string) []string {
	accepted := make([]string, 0, len(requests))
	for _, request := range requests {
		if strings.TrimSpace(request) == "" {
			continue
		}
		if _, err := parser.Parse(request); err == nil {
			accepted = append(accepted, request)
		}
	}
	return accepted
}
