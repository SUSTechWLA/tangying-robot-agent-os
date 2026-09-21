// context-render exposes the production renderer to reproducible offline evals.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"os"
	"strings"
)

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 65536), 8*1024*1024)
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	for scanner.Scan() {
		var req struct {
			Context   agentcontext.Document         `json:"context"`
			Style     string                        `json:"style"`
			Decision  *agentcontext.DecisionContext `json:"decision"`
			Factors   agentcontext.FactorSpec       `json:"factors"`
			RoundTrip bool                          `json:"round_trip"`
		}
		decoder := json.NewDecoder(strings.NewReader(scanner.Text()))
		decoder.UseNumber()
		if err := decoder.Decode(&req); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		if req.Decision != nil {
			result, err := agentcontext.RenderFactors(*req.Decision, req.Factors)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(2)
			}
			if req.RoundTrip {
				decoded, err := agentcontext.ParseFactorText(result.Text, req.Factors.Syntax)
				if err != nil {
					fmt.Fprintln(os.Stderr, err)
					os.Exit(2)
				}
				if err := enc.Encode(map[string]any{"rendering": result, "decoded": decoded}); err != nil {
					panic(err)
				}
			} else if err := enc.Encode(result); err != nil {
				panic(err)
			}
			continue
		}
		content, err := agentcontext.Render(req.Context, req.Style)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		if err := enc.Encode(map[string]any{"content": content, "sha256": agentcontext.Hash(content), "renderer_version": agentcontext.RendererVersion}); err != nil {
			panic(err)
		}
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}
