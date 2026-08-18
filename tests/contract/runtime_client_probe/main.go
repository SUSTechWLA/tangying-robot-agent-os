package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func main() {
	if len(os.Args) != 4 {
		panic("usage: runtime-client-probe ADDRESS PROFILE SUFFIX")
	}
	client, err := robotclient.New(robotclient.Config{
		Address:     os.Args[1],
		DevInsecure: true,
		Profile:     os.Args[2],
	})
	if err != nil {
		panic(err)
	}
	defer client.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	info, err := client.Info(ctx)
	if err != nil {
		panic(err)
	}
	result, err := client.Invoke(ctx, runtime.Command{
		SchemaVersion: "robot.v1",
		CommandID:     "probe-" + os.Args[3],
		TaskID:        "task-contract",
		Capability:    runtime.CapabilityPick,
		TargetRef:     "red-cup",
		Parameters: map[string]any{
			"objectId":      "red-cup",
			"destinationId": "right-bin",
			"action_chunk":  []any{map[string]any{"left_arm_1.pos": 10.0}},
		},
		Deadline:       time.Now().Add(30 * time.Second),
		Lease:          5 * time.Second,
		IdempotencyKey: "task-contract-pick-" + os.Args[3],
		SafetyProfile:  os.Args[2],
		ApprovalID:     "approval-contract",
	})
	if err != nil {
		panic(err)
	}
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{
		"adapter": info.Adapter,
		"result":  result,
	}); err != nil {
		panic(fmt.Errorf("encode result: %w", err))
	}
}
