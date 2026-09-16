// This executable decodes a `robot.faults.v1` document with the production Go
// contract decoder. The contract test feeds it what the real Python Robot
// Runtime publishes, so the publisher and the consumer cannot drift apart
// silently: a fault list gates capabilities, and a consumer that misreads it
// would either ignore a broken module or refuse a healthy robot.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
)

func main() {
	wire, err := io.ReadAll(os.Stdin)
	if err != nil {
		panic(fmt.Errorf("read faults document: %w", err))
	}
	out := map[string]any{"accepted": false}
	var document map[string]any
	if err := json.Unmarshal(wire, &document); err != nil {
		out["error"] = "not a JSON object: " + err.Error()
	} else if report, err := robotcontract.DecodeFaults(document); err != nil {
		out["error"] = err.Error()
	} else {
		out["accepted"] = true
		out["severity"] = report.Severity
		out["count"] = report.Count
		out["keys"] = report.Keys()
		out["safetyStopped"] = report.SafetyStopped()
		out["blocking"] = len(report.Blocking())
		out["unavailableCapabilities"] = report.UnavailableCapabilities
		if len(report.Faults) > 0 {
			out["firstInstruction"] = report.Faults[0].UserInstruction
			out["firstModule"] = report.Faults[0].ModuleID
		}
	}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		panic(fmt.Errorf("encode faults probe: %w", err))
	}
}
