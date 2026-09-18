// Command training-export quantifies a task archive and writes a training dataset.
//
// It answers three questions in order, and stops when one of them has a bad answer:
//
//  1. What is in the archive, in distinct requests rather than rows?
//  2. How much of it is actually usable — confirmed by the closed loop, and carrying
//     a real orchestration plan?
//  3. What does the split look like, and what is missing from it?
//
// Usage:
//
//	training-export -db agent.db                     # quantify only
//	training-export -db agent.db -out ./dataset      # quantify and write
//	training-export -db agent.db -out ./dataset -corpus corpus.txt
//
// Exit status is 0 when a dataset was written, 1 when the archive cannot support
// training yet, and 2 when the run itself failed. The middle case is the common one
// and it is not an error: it means "collect more data", and a pipeline that
// reported it as a crash would be ignored.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration/eval"
	"github.com/SUSTechWLA/tangying-robot-agent-os/training"
)

func main() {
	var (
		dbPath     = flag.String("db", "", "path to the Local Agent database (read-only)")
		outDir     = flag.String("out", "", "write the dataset here; omit to only quantify")
		corpusPath = flag.String("corpus", "", "one request per line, for harvesting refusals")
		provider   = flag.String("provider", "deterministic", "parser provider for the refusal harvest")
		baseURL    = flag.String("base-url", os.Getenv("AGENT_BASE_URL"), "OpenAI-compatible base URL")
		apiKey     = flag.String("api-key", os.Getenv("AGENT_API_KEY"), "OpenAI-compatible API key")
		model      = flag.String("model", os.Getenv("AGENT_MODEL"), "model name")
	)
	flag.Parse()
	if *dbPath == "" {
		fmt.Fprintln(os.Stderr, "-db is required")
		os.Exit(2)
	}

	ctx := context.Background()
	ledger, err := training.OpenLedger(ctx, *dbPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		os.Exit(2)
	}
	defer ledger.Close()

	records, err := ledger.Records(ctx)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		os.Exit(2)
	}

	quantification := training.Quantify(records)
	fmt.Print(quantification.Summary())

	dataset := training.Split(records)
	dataset.Refusals = append(dataset.Refusals, harvest(RecordsToRefusals(*corpusPath), *provider, *baseURL, *apiKey, *model)...)

	fmt.Printf("\n切分结果：\n")
	fmt.Printf("  SFT（已确认且有计划）  %4d\n", len(dataset.SFT))
	fmt.Printf("  拒绝样本               %4d\n", len(dataset.Refusals))
	fmt.Printf("  负样本                 %4d\n", len(dataset.Negative))
	if len(dataset.Excluded) > 0 {
		fmt.Printf("  被排除：\n")
		for reason, count := range dataset.Excluded {
			fmt.Printf("    %-28s %4d\n", reason, count)
		}
	}
	if len(dataset.Refusals) == 0 {
		// Said out loud because it is the failure mode that looks like success: a
		// dataset with positives and no refusals trains a model that never declines.
		fmt.Println("\n⚠ 拒绝样本为 0。用它做 SFT 会训出一个「永远不会说不」的模型；" +
			"请提供 -corpus 或先把被拒绝的请求记进账本。")
	}

	if *outDir == "" {
		os.Exit(exitFor(quantification))
	}
	if err := writeDataset(*outDir, dataset); err != nil {
		fmt.Fprintf(os.Stderr, "%v\n", err)
		os.Exit(2)
	}
	fmt.Printf("\n已写入 %s\n", *outDir)
	os.Exit(exitFor(quantification))
}

// exitFor maps the quantification to an exit status.
//
// Zero usable positives is "collect more data", which is a routine outcome and not
// a crash. A pipeline that called it a failure would train its operators to ignore
// the status.
func exitFor(quantification training.Quantification) int {
	if quantification.UsablePositives == 0 {
		return 1
	}
	return 0
}

// RecordsToRefusals reads a corpus file, one request per line.
func RecordsToRefusals(path string) []string {
	if path == "" {
		return nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "read corpus: %v\n", err)
		return nil
	}
	requests := make([]string, 0)
	for _, line := range strings.Split(string(raw), "\n") {
		if trimmed := strings.TrimSpace(line); trimmed != "" && !strings.HasPrefix(trimmed, "#") {
			requests = append(requests, trimmed)
		}
	}
	return requests
}

// harvest runs the refusal corpus through a parser, always including the eval
// case set's refusal half so the safety-critical samples are never absent just
// because somebody forgot a file.
func harvest(corpus []string, provider, baseURL, apiKey, model string) []training.Refusal {
	for _, testCase := range eval.DefaultCases {
		if testCase.MustRefuse {
			corpus = append(corpus, testCase.Request)
		}
	}
	if len(corpus) == 0 {
		return nil
	}
	parser := agent.NewParser(agent.Config{
		Provider: provider, BaseURL: baseURL, APIKey: apiKey, Model: model,
	})
	refusals := training.HarvestRefusals(parser, corpus)
	fmt.Printf("\n拒绝语料：%d 条请求，其中 %d 条被拒绝\n", len(corpus), len(refusals))
	return refusals
}

func writeDataset(directory string, dataset training.Dataset) error {
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return fmt.Errorf("create %s: %w", directory, err)
	}
	files := map[string]any{
		"sft.jsonl":      dataset.SFT,
		"refusals.jsonl": dataset.Refusals,
		"negative.jsonl": dataset.Negative,
	}
	for name, payload := range files {
		path := filepath.Join(directory, name)
		file, err := os.Create(path)
		if err != nil {
			return fmt.Errorf("create %s: %w", path, err)
		}
		encoder := json.NewEncoder(file)
		// Records are written one per line so a training loader can stream them and
		// so a diff of two exports is readable.
		writeErr := writeLines(encoder, payload)
		closeErr := file.Close()
		if writeErr != nil {
			return fmt.Errorf("write %s: %w", path, writeErr)
		}
		if closeErr != nil {
			return fmt.Errorf("write %s: %w", path, closeErr)
		}
	}
	manifest, err := json.MarshalIndent(map[string]any{
		"excluded": dataset.Excluded,
		"counts": map[string]int{
			"sft": len(dataset.SFT), "refusals": len(dataset.Refusals),
			"negative": len(dataset.Negative),
		},
	}, "", "  ")
	if err != nil {
		return fmt.Errorf("encode manifest: %w", err)
	}
	return os.WriteFile(filepath.Join(directory, "manifest.json"), append(manifest, '\n'), 0o644)
}

type encoder interface{ Encode(any) error }

func writeLines(encoder encoder, payload any) error {
	switch typed := payload.(type) {
	case []training.Record:
		for index := range typed {
			if err := encoder.Encode(typed[index]); err != nil {
				return err
			}
		}
	case []training.Refusal:
		for index := range typed {
			if err := encoder.Encode(typed[index]); err != nil {
				return err
			}
		}
	}
	return nil
}
