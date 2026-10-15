"""Offline independent validation of retained delegated Gazebo evidence; no network or robot actions."""
import copy, hashlib, json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/"scripts"))
from evaluate_long_horizon import validate_execution, timestamp_ms

root=Path(__file__).resolve().parent
case=sys.argv[1] if len(sys.argv)>1 else "host-inspection01"
run=root/case
output=root/(case+"-independent.json")
if output.exists():
    raise FileExistsError(output)
report={"schemaVersion":"research.delegated-gazebo.validation.v1","passed":False,
        "networkAccess":False,"robotActions":False,"rawFilesRewritten":False,
        "approvalBasis":"Parent objective grants authorize an edge-generated child plan; the derived projection is not a separately human-approved plan",
        "projection":"Keep original parent task command/observation identity; use durable child plan and events, prefix only child capability step IDs with child ID to match their actual legacy command step namespace; no physical observations or command evidence changed."}
try:
    manifest=json.loads((run/"manifest.json").read_text())
    for name,digest in manifest["files"].items():
        assert hashlib.sha256((run/name).read_bytes()).hexdigest()==digest, "raw file SHA differs: "+name
    report["rawFilesVerified"]=len(manifest["files"])
    report["rawBytesVerified"]=sum((run/name).stat().st_size for name in manifest["files"])
    task=json.loads((run/"final-task.json").read_text())
    report.update(taskId=task["id"], state=task["state"])
    prereg=json.loads((run/"preregistration.json").read_text())
    envelope=task["plan"]["capabilities"]["calls"]
    assert len(envelope)==1 and envelope[0]["tool"]=="edge.goal"
    assert envelope[0]["arguments"]["objective"]==prereg["objective"], "approved objective differs from preregistration"
    assert task["state"]=="SUCCEEDED" and task["approved"]
    states=json.loads((run/"durable-state-index.json").read_text())["states"]
    children=[]; checkpoint=None
    for state in states:
        raw=(run/state["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==state["sha256"]
        item=json.loads(raw)
        if state["aggregateId"].startswith("edge-child/"): children.append(item)
        elif state["aggregateId"].startswith("edge-goal/"): checkpoint=item
    assert len(children)==1, "this preregistered sample requires one immutable child plan"
    child=children[0]; child_id=child["id"]
    assert checkpoint and checkpoint["complete"] and not checkpoint.get("pending",False)
    proofs=[e["payload"]["evidence"] for e in task["events"] if e["type"]=="CAPABILITY_VERIFIED" and e.get("payload",{}).get("tool")=="edge.goal"]
    assert proofs and all(proof==checkpoint["proof"] for proof in proofs)
    proof=proofs[-1]
    assert proof["generation"]==1 and proof["steps"]<=prereg["objective"]["budget"]["maxSteps"]
    assert proof["reads"]<=prereg["objective"]["budget"]["maxReads"]
    assert len(proof["checks"])==len(prereg["objective"]["completion"])
    for row, expected in zip(proof["checks"], prereg["objective"]["completion"]):
        assert row["check"] == expected and row.get("satisfied") is True and row["value"] == expected["equals"], "completion proof differs from exact registered condition"
    declared=[e["payload"]["calls"] for e in task["events"] if e["type"]=="EDGE_PLAN_CREATED" and e["payload"].get("childId")==child_id]
    assert declared and all(calls==child["plan"]["capabilities"]["calls"] for calls in declared)
    mirrored=[e["payload"] for e in task["events"] if e["type"]=="EDGE_CHILD_EVENT" and e["payload"].get("childId")==child_id]
    for e in child["events"]:
        if e["type"]=="CAPABILITY_VERIFIED":
            assert any(m["kind"]==e["type"] and m["childStepId"]==e["stepId"] and m["detail"]==e["payload"] for m in mirrored)
    grants={g["tool"]:g for g in prereg["objective"]["grants"]}
    child_calls=child["plan"]["capabilities"]["calls"]
    for call in child_calls:
        assert call["tool"] in grants
        if call["tool"]=="robot.task":
            expected_request=grants["robot.task"]["argumentsSchema"]["properties"]["request"]["enum"][0]
            assert call["arguments"]=={"request":expected_request}
            assert call["legacyIntent"]["action"]=="home_route" and not call["legacyIntent"].get("sequence")
        else:
            assert call["arguments"]=={}
    child_verified=[e for e in child["events"] if e["type"]=="CAPABILITY_VERIFIED"]
    assert len(proof["completed"])==len(child_calls)==len(child_verified)
    for done,event in zip(proof["completed"],child_verified):
        assert done["childId"]==child_id and done["stepId"]==event["stepId"] and done["tool"]==event["payload"]["tool"]
    routes=[c["legacyIntent"]["routeRooms"] for c in child["plan"]["capabilities"]["calls"] if c["tool"]=="robot.task"]
    assert routes==[prereg["expectedRouteRooms"]], "immutable child plan lost expected route/repeat"
    projected=copy.deepcopy(task)
    projected["plan"]=copy.deepcopy(child["plan"])
    original=[]
    for event in task["events"]:
        if event["type"].startswith(("EDGE_","CAPABILITY_")): continue
        if event["type"]=="TOOL_ACTIVITY" and event.get("payload",{}).get("toolName")=="edge.goal": continue
        original.append(copy.deepcopy(event))
    for event in child["events"]:
        event=copy.deepcopy(event)
        if event.get("stepId"): event["stepId"]=child_id+"-"+event["stepId"]
        original.append(event)
    projected["events"]=sorted(original,key=lambda e:timestamp_ms(e["occurredAt"]))
    details={}; catalog=None
    for request in manifest["requests"]:
        if request["path"]=="/v1/robot/services" and request["method"]=="GET":catalog=json.loads((run/request["file"]).read_text())
        if "/observations/" in request["path"] and not request["path"].endswith(("/rgb","/depth")):
            detail=json.loads((run/request["file"]).read_text());details[detail["stepId"],detail["captureId"]]=detail
    assert catalog
    result=validate_execution(projected,projected["plan"],catalog,details,prereg["minimumArrivals"],False)
    arrival_steps={row["stepId"] for row in result["verifiedArrivals"]}
    arrival_poses=[d["snapshot"]["robotState"]["base_pose"] for (step,capture),d in details.items() if step in arrival_steps]
    max_span=max(math.dist(a[:2],b[:2]) for a in arrival_poses for b in arrival_poses)
    assert max_span >= prereg["minimumActualDisplacementM"], "captured arrival poses show insufficient physical displacement"
    report.update(maxArrivalPoseSpanM=max_span, actualTravelledM=None,
                  displacementInterpretation="largest planar separation of retained command-bound arrival poses; not traveled odometry distance or SLAM absolute accuracy")
    contexts=[r.get("context",{}) for r in child["plan"]["capabilities"].get("planningTrace",[])]
    assert any(c.get("model_request_json") for c in contexts), "actual GOAL model input not retained"
    report.update(result, taskId=task["id"],childId=child_id, state=task["state"], completionProof=proof,
                  routeRooms=routes, actualModelInputSnapshots=sum(bool(c.get("model_request_json")) for c in contexts),
                  passed=True)
except Exception as error:
    report.update(errorType=type(error).__name__, error=str(error))
output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({k:report[k] for k in ["taskId","passed","errorType","error"] if k in report},ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)
