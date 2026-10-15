"""One explicitly bounded delegated objective against the already running Gazebo episode."""
import json, time, sys, sqlite3, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "scripts"))
from evaluate_long_horizon import EvidenceAPI, collect_observations, TERMINAL
from console_session import install_loopback_opener

root = Path(__file__).resolve().parent
case = sys.argv[1] if len(sys.argv)>1 else "host-inspection01"
build_manifest = sys.argv[2] if len(sys.argv)>2 else "agent-build-final2.json"
out = root / case
out.mkdir(exist_ok=False)
install_loopback_opener()
api = EvidenceAPI("http://127.0.0.1:8927", out)
report = {"schemaVersion": "research.delegated-gazebo.v1", "physicalHardwareTested": False,
          "episodeId": 1016, "worldReset": False, "endpoint": "/v1/objectives", "passed": False,
          "startedAt": datetime.now(timezone.utc).isoformat()}
task = None
started = time.monotonic()
try:
    catalog = api("/v1/robot/services")
    nav = api("/v1/navigation/map")
    if not nav.get("ready") or nav.get("gridUnavailable"):
        raise AssertionError("actual mapped robot must be ready before creating the delegated goal")
    short_request = "从客厅出发，巡检卧室和卫生间，最后回到客厅"
    goal = short_request + "；执行前后读取导航状态，最后读取地图状态。每次导航均通过闭环执行器确认抵达并取得新鲜RGB-D观测。"
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    objective = {
        "schemaVersion": "robot.objective.v1", "robotId": catalog["robotId"], "goal": goal,
        "catalogRevision": catalog["goalCatalogRevision"],
        "grants": [{"tool": "robot.task", "maxExecutions": 1, "argumentsSchema": {
            "type": "object", "additionalProperties": False, "required": ["request"],
            "properties": {"request": {"type": "string", "enum": [short_request]}}}}]
            + [{"tool": tool, "argumentsSchema": empty} for tool in
               ["navigation.status", "mapping.status", "semantic.locations"]],
        "effects": ["READ", "PHYSICAL_MOTION"], "resources": ["robot"],
        "completion": [
            {"tool": "navigation.status", "arguments": {}, "path": ["mapId"], "equals": nav["mapId"]},
            {"tool": "navigation.status", "arguments": {}, "path": ["mapRevision"], "equals": nav["mapRevision"]},
            {"tool": "navigation.status", "arguments": {}, "path": ["localizationState"], "equals": "localized"}],
        "budget": {"maxPlans": 3, "maxSteps": 10, "maxReads": 120, "timeoutSeconds": 1200},
        "expiresAt": (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat().replace("+00:00", "Z")}
    prereg = {"registeredAt": datetime.now(timezone.utc).isoformat(), "objective": objective,
              "expectedRouteRooms": ["living_room", "bedroom", "bathroom", "living_room"],
              "minimumArrivals": 3, "minimumActualDisplacementM": 5,
              "completionPredicatesOnlyProve": "map identity and localization state; route completion requires separate original command/observation evidence",
              "sourceBuild": json.loads((root / build_manifest).read_text())}
    api.save("preregistration.json", prereg)
    task = api("/v1/objectives", {"adapter": "gazebo", "idempotencyKey": "research-20261015-gazebo-002-"+case, "objective": objective})
    api.save("created-objective-task.json", api.last_raw, raw=True)
    path = "/v1/tasks/"+task["id"]
    if task.get("approved"):
        raise AssertionError("new objective must be unapproved")
    report.update(taskId=task["id"], goal=goal, expectedRouteRooms=prereg["expectedRouteRooms"])
    task = api(path+"/approve", {"operator": "research-delegated-gazebo"})
    api.save("approved-objective-task.json", api.last_raw, raw=True)
    previous = None
    while time.monotonic()-started < 1500:
        task = api(path)
        if task["state"] != previous:
            previous = task["state"]
            print(json.dumps({"taskId": task["id"], "state": previous}), flush=True)
        if task["state"] in TERMINAL:
            break
        time.sleep(1)
    else:
        raise TimeoutError("delegated objective did not reach a terminal state")
    api.save("terminal-task.json", api.last_raw, raw=True)
    time.sleep(10)
    task = api(path)
    api.save("final-task.json", api.last_raw, raw=True)
    api.save("revisions.json", api(path+"/revisions"))
    details = collect_observations(api, path)
    report.update(state=task["state"], eventCount=len(task.get("events", [])), observations=len(details),
                  objectiveHostObserved=any(e["type"]=="EDGE_PLAN_CREATED" for e in task.get("events", [])),
                  providerCompletionProofObserved=any(e["type"]=="CAPABILITY_VERIFIED" and e.get("payload", {}).get("tool")=="edge.goal" for e in task.get("events", [])))
    connection = sqlite3.connect("file:"+str((root/"agent/agent.db").resolve())+"?mode=ro", uri=True)
    connection.execute("BEGIN")
    states = connection.execute("SELECT aggregate_id,version,data FROM fleet_graph_states WHERE aggregate_id LIKE ? OR aggregate_id LIKE ? ORDER BY aggregate_id",
                                ("edge-child/"+task["id"]+"%", "edge-goal/"+task["id"]+"/%")).fetchall()
    state_index = []
    for number, (aggregate, version, raw) in enumerate(states):
        raw = raw.encode() if isinstance(raw, str) else raw
        name = "durable-state-%02d.json" % number
        api.save(name, raw, raw=True)
        state_index.append({"aggregateId": aggregate, "version": version, "file": name,
                            "sha256": hashlib.sha256(raw).hexdigest()})
    connection.close()
    api.save("durable-state-index.json", {"source": "read-only SQLite transaction of this task's exact child/checkpoint aggregates", "states": state_index})
    # Retention is independent of acceptance. The root reviewer verifies ordered physical outcomes separately.
    report["physicalAcceptancePendingIndependentVerification"] = True
    report["executionSucceeded"] = task["state"] == "SUCCEEDED"
    report["passed"] = report["executionSucceeded"] and report["objectiveHostObserved"]
except Exception as error:
    report.update(errorType=type(error).__name__, error=str(error))
    if task is not None:
        try:
            task = api("/v1/tasks/"+task["id"])
            if task["state"] not in TERMINAL:
                api("/v1/tasks/"+task["id"]+"/cancel", {})
                report["cancellationRequested"] = True
            task = api("/v1/tasks/"+task["id"])
            api.save("final-known-task.json", api.last_raw, raw=True)
        except Exception as cleanup:
            report["cleanupError"] = str(cleanup)
finally:
    report.update(elapsedSeconds=time.monotonic()-started, endedAt=datetime.now(timezone.utc).isoformat())
    api.save("report.json", report)
    api.save("manifest.json", {"algorithm": "sha256", "files": api.files, "requests": api.requests})
    print(json.dumps(report, ensure_ascii=False), flush=True)
