"""Run one natural-language capability goal through the production task API.

Approval starts robot motion. Use a simulation or a commissioned safe test area.
Artifacts contain the immutable plan, event history and provider verification;
no direct mapping RPC is used to advance the task.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from console_session import headers as session_headers
from console_session import install_loopback_opener, resolve_token


def evaluate(base_url, request, output, timeout):
    output.mkdir(parents=True, exist_ok=False)
    install_loopback_opener()
    token = resolve_token(base_url=base_url)
    report = {"schemaVersion": "capability.goal.acceptance.v1", "request": request,
              "passed": False, "physicalCertification": False}

    def api(path, body=None):
        headers = session_headers(token, {"Content-Type": "application/json", "Origin": base_url})
        wire = None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(urllib.request.Request(base_url+path, data=wire,
                                    headers=headers), timeout=45) as response:
            return json.load(response)

    task = None
    try:
        catalog = api('/v1/robot/services')
        (output/'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n')
        task = api('/v1/tasks', {"request": request, "adapter": "gazebo"})
        assert task.get('plan', {}).get('capabilities'), 'task did not use capability goal architecture'
        report['taskId'] = task['id']
        (output/'draft-plan.json').write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
        task = api('/v1/tasks/'+task['id']+'/approve', {"operator": "capability-acceptance"})
        (output/'approved-plan.json').write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
        (output/'model-routes.json').write_text(json.dumps(api('/v1/config/status'), ensure_ascii=False, indent=2)+'\n')
        deadline = time.monotonic()+timeout
        state = None
        while time.monotonic() < deadline:
            task = api('/v1/tasks/'+task['id'])
            if task['state'] != state:
                state = task['state']
                print(json.dumps({"taskId": task['id'], "state": state}, ensure_ascii=False), flush=True)
            if state in {'SUCCEEDED', 'RECOVERABLE_FAILURE', 'CANCELLED', 'FAILED', 'WAITING_USER', 'SAFETY_STOPPED'}:
                break
            time.sleep(2)
        assert task['state'] == 'SUCCEEDED', f"task ended in {task['state']}"
        calls = task['plan']['capabilities']['calls']
        verified = [event for event in task['events'] if event['type'] == 'CAPABILITY_VERIFIED']
        assert len(verified) == len(calls), 'missing provider completion evidence'
        if any(call['tool'] == 'robot.task' for call in calls):
            assert any(e['type'] == 'TOOL_ACTIVITY' and e.get('payload', {}).get('toolName') == 'verify_arrival'
                       and e.get('payload', {}).get('activityStatus') == 'CONFIRMED'
                       for e in task['events']), 'navigation arrival was not verified'
        report.update(passed=True, state=task['state'], verifiedSteps=len(verified),
                      planSource=task['plan']['source'], tools=[call['tool'] for call in calls],
                      verifiedEvidence=[{'stepId': e.get('stepId'), 'tool': e['payload'].get('tool'),
                                         'evidence': e['payload'].get('evidence')} for e in verified])
    except (AssertionError, ValueError, OSError, urllib.error.URLError) as error:
        report['error'] = str(error)
        if task and task["state"] not in {"RECOVERABLE_FAILURE", "CANCELLED", "FAILED", "WAITING_USER", "SAFETY_STOPPED", "SUCCEEDED"}:
            try:
                api('/v1/tasks/'+task['id']+'/cancel', {})
                time.sleep(2)
                task = api('/v1/tasks/'+task['id'])
            except (OSError, ValueError, urllib.error.URLError) as cleanup:
                report['cleanupError'] = str(cleanup)
    finally:
        if task:
            (output/'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8897')
    parser.add_argument('--request', default='运行标定，建图，然后去厨房')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=1800)
    args = parser.parse_args()
    raise SystemExit(0 if evaluate(args.base_url.rstrip('/'), args.request, args.output, args.timeout) else 1)
