package tasks_test

import (
	"bytes"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestExperienceUsesHumanToolLanguageAndFiltersSecrets(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-1", CurrentRevision: 2, AggregateVersion: 7},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-1", Revision: 2, Request: "把方块放到右侧", Understanding: "2号机器人把方块放到右侧目标区",
		}},
		Activities: []tasks.ToolActivityInput{{
			ToolName: "manipulation.pick", Status: "AWAITING_EVIDENCE", RobotID: "robot-2", StepID: "receiver",
			Arguments: map[string]any{"targetRef": "red-block", "bearerToken": "secret-bearer", "password": "secret-password"},
			Display:   tasks.ToolDisplay{DisplayName: "拿稳物品", Purpose: "安全拿起红色方块", SafeArguments: []string{"targetRef"}},
			CommandID: "cmd-2", CatalogRevision: "catalog-2", FencingToken: 3,
		}},
	})
	if view.SchemaVersion != "task.experience.v1" || view.Activities[0].DisplayName != "拿稳物品" ||
		view.Activities[0].StatusText != "动作已结束，正在确认环境和相机证据" {
		t.Fatalf("view=%#v", view)
	}
	if view.Activities[0].SafeArguments["targetRef"] != "red-block" || len(view.Activities[0].SafeArguments) != 1 {
		t.Fatalf("safe arguments=%#v", view.Activities[0].SafeArguments)
	}
	wire, _ := json.Marshal(view)
	for _, forbidden := range [][]byte{[]byte("secret-bearer"), []byte("secret-password"), []byte("bearerToken"), []byte("password")} {
		if bytes.Contains(wire, forbidden) {
			t.Fatalf("experience leaked secret field/value %q: %s", forbidden, wire)
		}
	}
	if view.Professional.Activities[0].ToolName != "manipulation.pick" || view.Professional.Activities[0].CommandID != "cmd-2" {
		t.Fatalf("professional details=%#v", view.Professional)
	}
}

func TestExperienceProjectsToolAndSensorSynchronization(t *testing.T) {
	synchronization := tasks.ActivitySynchronization{
		EpisodeID: "robocasa-handoff-v1:4", BasisCaptureID: "capture-4-36-80",
		LatestCaptureID: "capture-4-40-84", BasisWorldRevision: 100,
		LatestWorldRevision: 112, SensorFreshness: "FRESH",
	}
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-sync", CurrentRevision: 1},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-sync", Revision: 1,
		}},
		Activities: []tasks.ToolActivityInput{
			{ToolName: "manipulation.pick", Status: "RUNNING", Synchronization: synchronization},
			{ToolName: "manipulation.pick", Status: "AWAITING_EVIDENCE", Synchronization: tasks.ActivitySynchronization{SensorFreshness: "FROZEN"}},
			{ToolName: "manipulation.pick", Status: "CONFIRMED", EvidenceIDs: []string{"world/112"}, Synchronization: synchronization},
		},
	})
	if view.Activities[0].StatusText != "机器人正在执行，环境与相机正在同步更新" {
		t.Fatalf("running status=%q", view.Activities[0].StatusText)
	}
	if view.Activities[1].StatusText != "动作状态已收到，正在重新连接环境数据" {
		t.Fatalf("frozen status=%q", view.Activities[1].StatusText)
	}
	if view.Activities[2].EvidenceText != "环境和传感器已经确认动作结果" {
		t.Fatalf("confirmed evidence=%q", view.Activities[2].EvidenceText)
	}
	professional := view.Professional.Activities[2].Synchronization
	if professional.LatestCaptureID != synchronization.LatestCaptureID || professional.LatestWorldRevision != 112 {
		t.Fatalf("professional synchronization=%#v", professional)
	}
}

func TestUnadvancedEvidenceCannotSatisfyRevisionStep(t *testing.T) {
	record := tasks.RevisionRecord{Revision: tasks.TaskRevision{Steps: []tasks.RevisionStep{{
		StepID: "sender", Status: tasks.StepAwaitingEvidence,
	}}}}
	tasks.OverlayActivityStatuses(&record, []tasks.ToolActivityInput{{
		StepID: "sender", Status: "CONFIRMED", EvidenceIDs: []string{"observation-9"},
		Synchronization: tasks.ActivitySynchronization{
			BasisCaptureID: "capture-9", LatestCaptureID: "capture-9",
			BasisWorldRevision: 42, LatestWorldRevision: 42, SensorFreshness: "FROZEN",
		},
	}})
	if record.Revision.Steps[0].Status != tasks.StepAwaitingEvidence {
		t.Fatalf("unadvanced status=%q", record.Revision.Steps[0].Status)
	}

	tasks.OverlayActivityStatuses(&record, []tasks.ToolActivityInput{{
		StepID: "sender", Status: "CONFIRMED", EvidenceIDs: []string{"observation-10"},
		Synchronization: tasks.ActivitySynchronization{
			BasisCaptureID: "capture-9", LatestCaptureID: "capture-10",
			BasisWorldRevision: 42, LatestWorldRevision: 43, SensorFreshness: "FRESH",
		},
	}})
	if record.Revision.Steps[0].Status != tasks.StepSatisfied {
		t.Fatalf("advanced status=%q", record.Revision.Steps[0].Status)
	}
}

func TestExperienceCursorFollowsLatestDurableTaskEvent(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{
			ID:               "task-cursor",
			CurrentRevision:  1,
			AggregateVersion: 1,
			Events: []tasks.TaskEvent{
				{Sequence: 3, Type: "TOOL_ACTIVITY"},
				{Sequence: 11, Type: "TOOL_ACTIVITY"},
				{Sequence: 7, Type: "TOOL_ACTIVITY"},
			},
		},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-cursor", Revision: 1,
		}},
	})

	wire, err := json.Marshal(view)
	if err != nil {
		t.Fatal(err)
	}
	var public map[string]any
	if err := json.Unmarshal(wire, &public); err != nil {
		t.Fatal(err)
	}
	if public["cursor"] != float64(11) {
		t.Fatalf("cursor=%v, want latest durable event sequence 11; experience=%s", public["cursor"], wire)
	}
}

func TestExperienceExplainsPolicyControlWithoutExposingActionChunk(t *testing.T) {
	activities := tasks.ToolActivitiesFromEvents([]tasks.TaskEvent{{
		Type: "TOOL_ACTIVITY", Payload: map[string]any{
			"toolName": "manipulation.pick", "activityStatus": "AWAITING_EVIDENCE",
			"robotId": "robot-1", "stepId": "sender", "commandId": "cmd-policy",
			"arguments": map[string]any{
				"action_chunk": []any{map[string]any{"left_arm_gripper.pos": 50.0}},
				"policy_execution": map[string]any{
					"policyId": "tabletop-vla", "policyVersion": "3", "framework": "vla",
					"artifactSha256": "abcdef0123456789secret-tail", "manifestRevision": "manifest-9",
					"inferenceId": "infer-9", "observationId": "obs-9",
				},
			},
		},
	}}, nil)
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task:       &tasks.Task{ID: "task-policy", CurrentRevision: 1},
		Revision:   tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{TaskID: "task-policy", Revision: 1}},
		Activities: activities,
	})
	if view.Activities[0].ControlMethod != "视觉语言动作模型" || view.Activities[0].ControlStage != "等待环境确认" {
		t.Fatalf("activity = %#v", view.Activities[0])
	}
	policyEvidence := view.Professional.Activities[0].Policy
	if policyEvidence == nil || policyEvidence.PolicyID != "tabletop-vla" || policyEvidence.ArtifactHashPrefix != "abcdef012345" {
		t.Fatalf("policy evidence = %#v", policyEvidence)
	}
	wire, _ := json.Marshal(view)
	if bytes.Contains(wire, []byte("action_chunk")) || bytes.Contains(wire, []byte("left_arm_gripper.pos")) || bytes.Contains(wire, []byte("secret-tail")) {
		t.Fatalf("policy internals leaked: %s", wire)
	}
}

func TestExperienceProjectionMatchesPersistedEventReplay(t *testing.T) {
	events := []tasks.TaskEvent{
		{Sequence: 3, Type: "TOOL_ACTIVITY", Payload: map[string]any{"toolName": "manipulation.place", "activityStatus": "RUNNING", "robotId": "robot-2", "stepId": "receiver", "taskRevision": float64(2), "aggregateVersion": float64(8)}},
		{Sequence: 4, Type: "TOOL_ACTIVITY", Payload: map[string]any{"toolName": "manipulation.place", "activityStatus": "CONFIRMED", "robotId": "robot-2", "stepId": "receiver", "taskRevision": float64(2), "aggregateVersion": float64(9), "evidenceIds": []any{"observation-9"}, "synchronization": map[string]any{"episodeId": "scene:2", "basisCaptureId": "capture-8", "latestCaptureId": "capture-9", "basisWorldRevision": float64(8), "latestWorldRevision": float64(9), "sensorFreshness": "FRESH"}}},
	}
	wire, err := json.Marshal(events)
	if err != nil {
		t.Fatal(err)
	}
	var replayed []tasks.TaskEvent
	if err := json.Unmarshal(wire, &replayed); err != nil {
		t.Fatal(err)
	}
	project := func(stream []tasks.TaskEvent) tasks.TaskExperience {
		record := tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-replay", Revision: 2, Understanding: "2号机器人把红色方块放到右侧目标区",
			Steps: []tasks.RevisionStep{{StepID: "receiver", RobotID: "robot-2", Status: tasks.StepPending}},
		}}
		activities := tasks.ToolActivitiesFromEvents(stream, nil)
		tasks.OverlayActivityStatuses(&record, activities)
		return tasks.ProjectExperience(tasks.ExperienceInput{
			Task:     &tasks.Task{ID: "task-replay", CurrentRevision: 2, AggregateVersion: 9},
			Revision: record, Activities: activities,
		})
	}
	if live, replay := project(events), project(replayed); !reflect.DeepEqual(live, replay) || live.Steps[0].Status != tasks.StepSatisfied || live.Activities[1].EvidenceText == "" {
		t.Fatalf("experience replay mismatch: live=%#v replay=%#v", live, replay)
	}
}

func TestExperienceKeepsHarnessEvidenceInCollapsedProfessionalDetails(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-evidence", CurrentRevision: 2, AggregateVersion: 9},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-evidence", Revision: 2, Steps: []tasks.RevisionStep{{
				StepID: "sender", Status: tasks.StepSatisfied,
				HarnessEvidenceIDs: []string{"robot-1/scene/42"},
			}},
		}},
	})
	if len(view.Professional.StepEvidence) != 1 || view.Professional.StepEvidence[0].EvidenceIDs[0] != "robot-1/scene/42" {
		t.Fatalf("professional evidence=%#v", view.Professional.StepEvidence)
	}
}

func TestConfirmedToolActivityCannotEraseCoordinatorHarnessEvidence(t *testing.T) {
	record := tasks.RevisionRecord{Revision: tasks.TaskRevision{Steps: []tasks.RevisionStep{{
		StepID: "sender", Status: tasks.StepSatisfied,
		HarnessEvidenceIDs: []string{"world/observation/42"},
	}}}}
	tasks.OverlayActivityStatuses(&record, []tasks.ToolActivityInput{{
		StepID: "sender", Status: "CONFIRMED",
	}})
	if got := record.Revision.Steps[0].HarnessEvidenceIDs; len(got) != 1 || got[0] != "world/observation/42" {
		t.Fatalf("confirmed activity erased Harness evidence: %v", got)
	}
}

func TestSelectExperienceRevisionShowsConfirmedUpdateWaitingForSafePoint(t *testing.T) {
	task := &tasks.Task{ID: "task-wait", CurrentRevision: 1, RevisionState: tasks.RevisionWaitingSafePoint}
	history := []tasks.RevisionRecord{
		{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{TaskID: task.ID, Revision: 1}},
		{Status: tasks.RevisionWaitingSafePoint, Revision: tasks.TaskRevision{TaskID: task.ID, Revision: 2}},
	}
	record, visibleRevision, err := tasks.SelectExperienceRevision(task, history)
	if err != nil || record.Status != tasks.RevisionWaitingSafePoint || visibleRevision != 2 {
		t.Fatalf("record=%#v visible=%d err=%v", record, visibleRevision, err)
	}
}

func TestExperienceExplainsCompletedUpdateJourneyAfterRefresh(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-journey", CurrentRevision: 2, AggregateVersion: 9},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-journey", Revision: 2,
		}, Events: []tasks.RevisionLifecycleEvent{
			{Revision: 2, Status: tasks.RevisionProposed},
			{Revision: 2, Status: tasks.RevisionWaitingSafePoint},
			{Revision: 2, Status: tasks.RevisionActive},
		}},
	})
	joined := strings.Join(view.UpdateJourney, " ")
	for _, want := range []string{"理解", "安全动作", "启用"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("journey=%q missing %q", joined, want)
		}
	}
}

func TestExperienceNamesBlueTargetWithoutInternalReference(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-blue", CurrentRevision: 2},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-blue", Revision: 2, Steps: []tasks.RevisionStep{{
				Action: "pick_and_place", RobotID: "robot-2", ResourceID: "red-block",
				RequiredPostcondition: "red-block in target_zone/right_side/blue-target_zone",
			}},
		}},
	})
	if got := view.Steps[0].Explanation; got != "2号机器人把红色方块放到右侧蓝色垫子" {
		t.Fatalf("explanation=%q", got)
	}
}

func TestBasicRecoveryGuidanceSupportsLocalBrainFailuresAndSafeUpdates(t *testing.T) {
	failed := tasks.BasicRecoveryGuidance(tasks.RevisionActive, []tasks.ToolActivityInput{{Status: "FAILED"}})
	if failed == nil || !strings.Contains(failed.RobotSafetyState, "停止推进") {
		t.Fatalf("failed guidance=%#v", failed)
	}
	waiting := tasks.BasicRecoveryGuidance(tasks.RevisionWaitingSafePoint, nil)
	if waiting == nil || !strings.Contains(waiting.RobotSafetyState, "安全动作") {
		t.Fatalf("waiting guidance=%#v", waiting)
	}
	if healthy := tasks.BasicRecoveryGuidance(tasks.RevisionActive, nil); healthy != nil {
		t.Fatalf("healthy guidance=%#v", healthy)
	}
}

func TestExperienceFallsBackWithoutEchoingUnknownToolNameInDefaultView(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task:       &tasks.Task{ID: "task-1", CurrentRevision: 1, AggregateVersion: 1},
		Revision:   tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{TaskID: "task-1", Revision: 1}},
		Activities: []tasks.ToolActivityInput{{ToolName: "vendor.secret_raw_tool", Status: "RUNNING"}},
	})
	if view.Activities[0].DisplayName != "机器人能力" || view.Activities[0].Purpose != "机器人正在执行当前步骤" {
		t.Fatalf("fallback=%#v", view.Activities[0])
	}
	if bytes.Contains([]byte(view.Activities[0].DisplayName+view.Activities[0].Purpose), []byte("vendor.secret_raw_tool")) {
		t.Fatal("default view echoed an unknown raw tool name")
	}
}

func TestExperienceUsesServerOwnedKnownToolFallbackAndLabelsStep(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-1", CurrentRevision: 1, AggregateVersion: 1},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-1", Revision: 1, Steps: []tasks.RevisionStep{{StepID: "sender", RobotID: "robot-1"}},
		}},
		Activities: []tasks.ToolActivityInput{{ToolName: "manipulation.pick", Status: "RUNNING", StepID: "sender"}},
	})
	if view.Activities[0].DisplayName != "拿稳物品" || view.Activities[0].Purpose == "" ||
		view.Steps[0].CapabilityLabel != "拿稳物品" {
		t.Fatalf("view=%#v", view)
	}
}

func TestExperienceExplainsRealHandoffStepsWithoutTechnicalIdentifiers(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &tasks.Task{ID: "task-handoff", CurrentRevision: 1, AggregateVersion: 1},
		Revision: tasks.RevisionRecord{Status: tasks.RevisionActive, Revision: tasks.TaskRevision{
			TaskID: "task-handoff", Revision: 1, Understanding: "1号机器人把red-block放到handoff-zone，2号机器人送到right-target-zone",
			ChangeSet: tasks.ChangeSet{Retained: []string{"intent-000/internal-hash"}, Changed: []string{"intent-001/internal-hash"}, Paused: []string{"old/internal-hash"}},
			Steps: []tasks.RevisionStep{
				{StepID: "intent-000/internal-hash", Action: "pick_and_place", RobotID: "robot-1", ResourceID: "red-block", RequiredPostcondition: "red-block in handoff_zone"},
				{StepID: "intent-001/internal-hash", Action: "fetch", RobotID: "robot-2", ResourceID: "red-block", RequiredPostcondition: "red-block in target_zone/right_side"},
			},
		}},
	})

	if view.Steps[0].Explanation != "1号机器人把红色方块放到交接区" {
		t.Fatalf("sender explanation=%q", view.Steps[0].Explanation)
	}
	if view.Steps[1].Explanation != "2号机器人把红色方块送到右侧目标区" {
		t.Fatalf("receiver explanation=%q", view.Steps[1].Explanation)
	}
	if view.Understanding != "1号机器人把红色方块放到交接区，2号机器人送到右侧目标区" || view.Headline != view.Understanding {
		t.Fatalf("understanding=%q headline=%q", view.Understanding, view.Headline)
	}
	if len(view.ChangePreview.Retained) != 1 || view.ChangePreview.Retained[0] != "1号机器人把红色方块放到交接区" ||
		len(view.ChangePreview.Changed) != 1 || view.ChangePreview.Changed[0] != "2号机器人把红色方块送到右侧目标区" ||
		len(view.ChangePreview.Paused) != 1 || view.ChangePreview.Paused[0] != "机器人会先完成上一版正在执行的安全动作" {
		t.Fatalf("change preview leaked technical steps: %#v", view.ChangePreview)
	}
	for _, step := range view.Steps {
		if bytes.Contains([]byte(step.Explanation), []byte("red-block")) || bytes.Contains([]byte(step.Explanation), []byte("target-zone")) ||
			bytes.Contains([]byte(step.Explanation), []byte("target_zone")) || bytes.Contains([]byte(step.Explanation), []byte("handoff_")) {
			t.Fatalf("technical identifier leaked into user explanation: %q", step.Explanation)
		}
	}
}
