"""Export immutable, small research summaries; retain large original bytes locally."""
import csv, hashlib, json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
root=Path(__file__).resolve().parent
out=Path("docs/experiments/evidence/2026-10-15-single-robot-robustness/gazebo")
out.mkdir(parents=True,exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
def save(name,data):
    (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
def copy(source,name):
    target=out/name
    if target.exists():raise FileExistsError(target)
    shutil.copy2(source,target)
def write_csv(name,rows):
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with (out/name).open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows(rows)
def seconds(start,end):
    return (datetime.fromisoformat(end.replace("Z","+00:00"))-datetime.fromisoformat(start.replace("Z","+00:00"))).total_seconds()
cases=[("preparation01","provider_cold_mapping","initial","preparation01-independent-no-fault.json"),
       ("inspection01","planning_failed_no_task","agent-build-upgrade.json",None),
       ("inspection02","legacy_frozen_long_inspection","agent-build-final2.json","inspection02-independent.json"),
       ("host-inspection01","delegated_host_failed_before_physical_dispatch","agent-build-final2.json","host-inspection01-independent.json"),
       ("host-inspection02","delegated_host_inspection","agent-build-final3.json","host-inspection02-independent.json")]
rows=[];step_rows=[];arrival_rows=[];raw_roots=[];chain=[]
for case,mode,build,validation_file in cases:
    folder=root/case;report=load(folder/"report.json")
    terminal=next((folder/name for name in ("final-task.json","final-known-task.json") if (folder/name).exists()),None)
    task=load(terminal) if terminal else None
    independent=load(root/validation_file) if validation_file else None
    row={"case":case,"mode":mode,"physical_episode":1016,"task_id":task["id"] if task else "",
         "task_state":task["state"] if task else "NO_TASK_CREATED","harness_passed":report.get("passed",False),
         "independent_passed":independent.get("passed",False) if independent else "not_executed",
         "harness_elapsed_seconds":report.get("elapsedSeconds"),
         "harness_recorded_utc":report.get("startedAt") or datetime.fromtimestamp((folder/"run-config.json").stat().st_mtime,timezone.utc).isoformat(),
         "harness_recorded_utc_source":"runner_startedAt" if report.get("startedAt") else "original_run_config_file_mtime",
         "task_created_utc":task.get("createdAt") if task else "","approved_utc":task.get("approvedAt") if task else "",
         "completion_event_utc":"","approved_to_completion_event_seconds":"", "actual_travelled_m":"not_measured",
         "provider_reported_mapping_travelled_m":"", "reported_mapping_frame_count":"", "canonical_map_points":"",
         "automatic_task_recovery":report.get("automaticTaskRecoveryObserved",False),
         "request":task.get("request") if task else report.get("request"),"source_build_record":build,
         "binary_sha256":load(root/"source-provenance-before-preparation.json")["binarySha256"] if build=="initial" else load(root/build)["binarySha256"],
         "map_id":"scan-73e5d48169a5","map_revision":"830264795090367ed40379d1106d3d0eefd2a47690b07b4258c7ff32468fc076",
         "model_provider":"openai-compatible","model_endpoint":"https://api.deepseek.com","model":"deepseek-flash",
         "physical_hardware_tested":False,"error":report.get("error","")}
    if mode.startswith("delegated"):
        row["request"]=report.get("goal")
    validated=(independent.get("execution",independent) if independent else {})
    arrivals=validated.get("verifiedArrivals",[])
    row.update(verified_capabilities=validated.get("verifiedCapabilities",0),verified_child_steps=validated.get("verifiedChildSteps",0),
               arrival_confirmations=len(arrivals),max_arrival_position_error_m=max((a["positionErrorM"] for a in arrivals),default=""),
               max_arrival_yaw_error_rad=max((a["yawErrorRad"] for a in arrivals),default=""))
    if task:
        events=task["events"]
        completed=[e["occurredAt"] for e in events if e["type"]=="LOCAL_RUN_SUCCEEDED"]
        if completed:
            row["completion_event_utc"]=completed[-1]
            row["approved_to_completion_event_seconds"]=seconds(task["approvedAt"],completed[-1])
        for event in events:
            if event["type"]=="CAPABILITY_VERIFIED" and event.get("payload",{}).get("tool")=="mapping.build":
                result=event["payload"].get("operationResult",{});row.update(provider_reported_mapping_travelled_m=result.get("travelledM"),reported_mapping_frame_count=result.get("frameCount"),canonical_map_points=result.get("pointCount"))
        grouped={}
        for event in events:
            payload=event.get("payload") or {}
            if event["type"]=="TOOL_ACTIVITY" and event.get("stepId"):
                grouped.setdefault(event["stepId"],[]).append(event)
        for step,items in grouped.items():
            start=next((e for e in items if e["payload"].get("activityStatus") in {"SENDING","RUNNING"}),items[0]);end=items[-1]
            step_rows.append({"case":case,"task_id":task["id"],"step_id":step,"tool":end["payload"].get("toolName"),"command_id":end["payload"].get("commandId"),"start_utc":start["occurredAt"],"last_utc":end["occurredAt"],"wall_seconds":seconds(start["occurredAt"],end["occurredAt"]),"last_status":end["payload"].get("activityStatus"),"source":"original parent TOOL_ACTIVITY events"})
        if case=="inspection02":
            chain=[e for e in events if e["type"].startswith(("CAPABILITY_READ_","ops.")) or e["type"] in {"CAPABILITY_CALL","CAPABILITY_FAILED","CAPABILITY_VERIFIED"} and e.get("stepId")=="rev-1-cap-01"]
        copy(terminal,case+"-terminal-task.json")
    manifest=load(folder/"manifest.json")
    raw_roots.append({"case":case,"path":str(folder),"manifestSha256":sha(folder/"manifest.json"),"fileCount":len(manifest["files"]),"rawBytes":sum((folder/name).stat().st_size for name in manifest["files"]),"files":manifest["files"]})
    details={}
    for req in manifest["requests"]:
        if "/observations/" in req["path"] and not req["path"].endswith(("/rgb","/depth")):
            d=load(folder/req["file"]);details[d["stepId"],d["captureId"]]=d
    evidence_by_step={e["stepId"]:e for e in validated.get("childEvidence",[])}
    for arrival in arrivals:
        evidence=evidence_by_step[arrival["stepId"]];detail=details[arrival["stepId"],evidence["captureId"]]
        arrival_rows.append({"case":case,"task_id":task["id"],"step_id":arrival["stepId"],"capture_id":detail["captureId"],"observation_id":detail["id"],"observed_at_unix_ms":detail["observedAtUnixMs"],"position_error_m":arrival["positionErrorM"],"yaw_error_rad":arrival["yawErrorRad"],"rgb_sha256":detail.get("rgbSha256"),"depth_sha256":detail.get("depthSha256"),"base_pose_wxyz":json.dumps(detail["snapshot"]["robotState"]["base_pose"]),"raw_bundle":str(folder),"interpretation":"command-relative residual, not SLAM absolute localization accuracy"})
    rows.append(row)
    for name in ["report.json","manifest.json","run-config.json","preregistration.json","durable-state-index.json"]:
        if (folder/name).exists():copy(folder/name,case+"-"+name)
    for file in folder.glob("durable-state-[0-9]*.json"):copy(file,case+"-"+file.name)
    if validation_file:copy(root/validation_file,validation_file)
write_csv("tasks.csv",rows);write_csv("steps.csv",step_rows);write_csv("arrivals.csv",arrival_rows)
save("fault-recovery-chain.json",chain);save("raw-evidence-index.json",{"localRawBytesOnly":True,"largeEvidenceDistributedWithGit":False,"bundles":raw_roots})
for name in ["environment-inventory.json","model-routes-before.json","source-provenance-before-preparation.json","agent-build-upgrade.json","agent-build-final2.json","agent-build-final3.json","controlled-agent-upgrade.json","controlled-final-agent-update.json","controlled-final3-agent-update.json","host01-pre-update-reconciliation.json","planning-attempt-adb3213caa1c9f65ef7883053e28fbc0.json","faults.jsonl","inspection-preregistration.json","preparation01-independent.json"]:
    copy(root/name,name)
copy(root.parent/"single-robot-robustness-20261015-gazebo-001/environment-failure.json","environment-attempt001-failed.json")
for name in ["run_host_inspection.py","revalidate_host_inspection.py","export_research_index.py"]:copy(root/name,"frozen-"+name)
save("summary.json",{"schemaVersion":"research.single-robot-gazebo.v1","recordedAt":datetime.now(timezone.utc).isoformat(),"physicalHardwareTested":False,"backend":"actual Gazebo + ROS2 + RTAB-Map + Nav2","physicalEpisode":1016,"runtimeRestartedWithinEpisode":False,"agentRestartedAtTerminalBoundaries":True,"notIndependentIIDRepeats":True,"aggregateSuccessRateIntentionallyNotComputed":True,"cases":rows,"importantLimits":["single fixed home scene and one ground mobile manipulator","no new grasp/place test in this campaign","mapping frame count/travel from provider result; no per-internal-motion agent RGB-D verification","arrival residual relative to commanded target; not SLAM absolute accuracy","three implementation versions and different mission types must not be pooled as a success-rate sample","large image/map/source/binary/DB artifacts retained locally, not distributed with Git"],"rawEvidenceRoot":str(root),"readOnlyConsole":"http://127.0.0.1:8927/"})
files={str(p.relative_to(out)):{"sha256":sha(p),"bytes":p.stat().st_size} for p in sorted(out.rglob("*")) if p.is_file()}
save("manifest.json",{"algorithm":"sha256","files":files,"manifestDoesNotHashItself":True})
print(json.dumps({"path":str(out),"files":len(files),"bytes":sum(f["bytes"] for f in files.values()),"cases":[{"case":r["case"],"state":r["task_state"],"independent":r["independent_passed"]} for r in rows]},ensure_ascii=False))
