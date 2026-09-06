// This executable exercises production Go Agent interfaces against a Python
// PluginBackend served by the contract test. It has no replacement gRPC server,
// mock Runtime client, robot-model selection or hardware SDK dependency.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func errorText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func command(info runtime.Snapshot, skill runtime.CapabilityName, suffix string) runtime.Command {
	return runtime.Command{
		SchemaVersion: "robot.v1", CommandID: "probe-" + suffix,
		TaskID: "heterogeneous-contract", RobotID: info.RobotID,
		Capability: skill, Parameters: map[string]any{},
		Deadline: time.Now().Add(20 * time.Second), Lease: 5 * time.Second,
		IdempotencyKey:  "heterogeneous-contract/" + suffix,
		CatalogRevision: info.CatalogRevision,
	}
}

func probe(ctx context.Context, client *robotclient.Client) map[string]any {
	out := map[string]any{}
	info, err := client.Info(ctx)
	out["info"], out["infoError"] = info, errorText(err)
	if err != nil {
		return out
	}
	out["observeAvailable"] = info.CanExecute("observe_scene") == nil
	out["pickAvailable"] = info.CanExecute("manipulation.pick") == nil
	out["moveAvailable"] = info.CanExecute("arm.move") == nil
	out["navigateAvailable"] = info.CanExecute("navigation.navigate") == nil
	telemetry, err := client.Telemetry(ctx, "heterogeneous-contract")
	out["telemetry"], out["telemetryError"] = telemetry, errorText(err)
	out["capturedUnixMs"] = telemetry.ObservedAt.UnixMilli()
	intent := manipulation.Intent{
		Action: manipulation.ActionPickAndPlace, RobotID: info.RobotID,
		Object:      manipulation.EntitySelector{Category: "cup", Attributes: map[string]string{"color": "red"}},
		Source:      manipulation.EntitySelector{Category: "table"},
		Destination: manipulation.EntitySelector{Category: "storage_bin", Attributes: map[string]string{"side": "right"}},
	}
	grounded, err := client.Ground(ctx, intent)
	out["ground"], out["groundError"] = grounded, errorText(err)
	intent.Source = intent.Destination
	_, err = client.Ground(ctx, intent)
	out["wrongSourceError"] = errorText(err)
	observed, err := client.Invoke(ctx, command(info, runtime.CapabilityObserveScene, "observe"))
	out["observeResult"], out["observeError"] = observed, errorText(err)
	if info.CanExecute("arm.move") == nil {
		unapproved := command(info, runtime.CapabilityMoveArm, "unapproved")
		unapproved.Parameters = map[string]any{
			"action_chunk": []any{map[string]any{"slide.position": 0.1}},
		}
		result, invokeErr := client.Invoke(ctx, unapproved)
		out["unapprovedResult"], out["unapprovedError"] = result, errorText(invokeErr)
		stale := command(info, runtime.CapabilityMoveArm, "stale-catalog")
		stale.Parameters, stale.ApprovalID = unapproved.Parameters, "fixture-approval"
		stale.CatalogRevision = "obsolete-catalog"
		result, invokeErr = client.Invoke(ctx, stale)
		out["staleCatalogResult"], out["staleCatalogError"] = result, errorText(invokeErr)
		approved := command(info, runtime.CapabilityMoveArm, "approved")
		approved.Parameters, approved.ApprovalID = unapproved.Parameters, "fixture-approval"
		result, invokeErr = client.Invoke(ctx, approved)
		out["moveResult"], out["moveError"] = result, errorText(invokeErr)
	}
	return out
}

func planProbe(ctx context.Context, client *robotclient.Client) map[string]any {
	out := map[string]any{}
	info, err := client.Info(ctx)
	out["info"], out["infoError"] = info, errorText(err)
	if err != nil {
		return out
	}
	grounded, err := client.Ground(ctx, manipulation.Intent{
		Action: manipulation.ActionPickAndPlace, RobotID: info.RobotID,
		Object:      manipulation.EntitySelector{Category: "block", Attributes: map[string]string{"color": "red"}},
		Source:      manipulation.EntitySelector{Category: "table"},
		Destination: manipulation.EntitySelector{Category: "tray", Attributes: map[string]string{"color": "blue"}},
	})
	out["groundError"] = errorText(err)
	if err != nil {
		return out
	}
	grounded.TaskID, grounded.RobotID = "heterogeneous-full-plan", info.RobotID
	plan := manipulation.Plan(grounded, time.Now().Add(25*time.Second))
	out["planShapeError"] = errorText(plan.ValidateShape())
	steps := []map[string]any{}
	for _, step := range plan.Steps {
		command := agent.CommandForStep(grounded.TaskID, step)
		command.CatalogRevision = info.CatalogRevision
		if step.Skill == "manipulation.pick" || step.Skill == "manipulation.place" {
			// Fixture policy output uses the exact wire shape appended by the
			// production Edge policy boundary. No learned-policy claim is made.
			observation, observeErr := client.Telemetry(ctx, grounded.TaskID)
			if observeErr != nil {
				out["policyObservationError"] = errorText(observeErr)
				break
			}
			position := 0.5
			if step.Skill == "manipulation.place" {
				position = 0.8
			}
			command.Parameters["action_chunk"] = []any{map[string]any{"axis1.position": position}}
			command.Parameters["policy_execution"] = map[string]any{
				"policyId": "contract-fixture", "policyVersion": "1", "framework": "deterministic",
				"artifactSha256": "deterministic:contract-fixture", "manifestRevision": strings.Repeat("a", 64),
				"inferenceId": command.CommandID + "/policy", "observationId": observation.Reconstruction.ObservationID,
				"elapsedMs": 0.0,
			}
		}
		result, invokeErr := client.Invoke(ctx, command)
		steps = append(steps, map[string]any{
			"skill": step.Skill, "parameters": command.Parameters, "targetRef": command.TargetRef,
			"approvalId": command.ApprovalID, "result": result, "error": errorText(invokeErr),
		})
		if invokeErr != nil || !result.Success {
			break
		}
	}
	out["steps"] = steps
	final, err := client.Telemetry(ctx, grounded.TaskID)
	out["finalTelemetry"], out["finalTelemetryError"] = final, errorText(err)
	return out
}

func main() {
	if len(os.Args) < 2 || len(os.Args) > 3 {
		panic("usage: heterogeneous-runtime-probe ADDRESS [plan]")
	}
	client, err := robotclient.New(robotclient.Config{
		Address: os.Args[1], DevInsecure: true,
	})
	if err != nil {
		panic(err)
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	var result map[string]any
	if len(os.Args) == 3 && os.Args[2] == "plan" {
		result = planProbe(ctx, client)
	} else {
		result = probe(ctx, client)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		panic(fmt.Errorf("encode contract probe: %w", err))
	}
}
