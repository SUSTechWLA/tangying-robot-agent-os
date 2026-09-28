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


def evaluate(base_url, request, output, timeout, required_tools=(), forbidden_tools=(),
             required_source=None, required_arguments=None):
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
        draft_plan = task['plan']
        draft_tools = [call['tool'] for call in draft_plan['capabilities']['calls']]
        required_at = 0
        for tool in draft_tools:
            if required_at < len(required_tools) and tool == required_tools[required_at]:
                required_at += 1
        assert required_at == len(required_tools), f'plan omitted or reordered required tools: {required_tools}'
        assert not set(draft_tools).intersection(forbidden_tools), 'plan includes forbidden tools'
        assert required_source is None or draft_plan['source'] == required_source, 'unexpected plan source'
        assert isinstance(required_arguments or {}, dict), 'required arguments must be a JSON object'
        for tool, expected in (required_arguments or {}).items():
            assert isinstance(expected, dict), 'required tool arguments must be JSON objects'
            matching = [call for call in draft_plan['capabilities']['calls'] if call['tool'] == tool]
            assert matching and all(all(call['arguments'].get(key) == value for key, value in expected.items())
                                    for call in matching), f'{tool} arguments differ from required values'
        assert not task['approved'], 'new task was approved before operator review'
        task = api('/v1/tasks/'+task['id']+'/approve', {"operator": "capability-acceptance"})
        (output/'approved-plan.json').write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
        assert task['approved'] and task['plan'] == draft_plan, 'approval changed the frozen plan'
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
        assert task['approved'] and task['plan'] == draft_plan, 'executed plan differs from approved draft'
        assert any(e['type'] == 'TASK_APPROVED' for e in task['events']), 'missing approval event'
        calls = task['plan']['capabilities']['calls']
        verified = [event for event in task['events'] if event['type'] == 'CAPABILITY_VERIFIED']
        receipts = [event for event in task['events'] if event['type'] == 'CAPABILITY_RECEIPT']
        assert len(verified) == len(calls), 'missing provider completion evidence'
        provider_calls = [call for call in calls if call['tool'] != 'robot.task']
        assert len(receipts) == len(provider_calls), 'missing provider call receipt'
        assert [e['payload'].get('tool') for e in verified] == [c['tool'] for c in calls], 'verification order/tool differs from plan'
        assert [e.get('stepId') for e in receipts] == [e.get('stepId') for e in verified if e['payload'].get('tool') != 'robot.task'], 'receipt and verification steps differ'
        assert len({e.get('stepId') for e in verified}) == len(calls), 'duplicate step verification'
        assert all(e['payload'].get('evidence') for e in verified), 'empty provider evidence'
        for call, event in zip(calls, verified):
            if call['tool'] != 'mapping.build':
                continue
            evidence = event['payload']['evidence']
            assert evidence.get('state') == 'completed', 'mapping operation did not complete'
            active = evidence.get('activeMap') or {}
            assert active.get('mapId') == evidence.get('mapId') and active.get('mapRevision'), \
                'completed map was not independently activated at a named revision'
            if call['arguments'].get('mode') == 'explore':
                budget = call['arguments'].get('maxTravelM')
                if budget is not None:
                    measured = evidence.get('operationTravelledM', evidence.get('travelledM', float('inf')))
                    assert float(measured) <= float(budget)+1e-6, \
                        f'exploration travelled beyond {budget} m budget'
                legs = call['arguments'].get('maxLegs')
                if legs is not None:
                    assert 1 <= int((evidence.get('exploration') or {}).get('leg', 0)) <= int(legs), \
                        f'exploration exceeded {legs} leg budget'
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
    print(json.dumps({key: report[key] for key in ('taskId', 'state', 'planSource', 'tools', 'verifiedSteps', 'passed', 'error') if key in report}, ensure_ascii=False), flush=True)
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8897')
    parser.add_argument('--request', default='运行标定，建图，然后去厨房')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--required-tools', nargs='*', default=[], help='ordered required tool subsequence checked before approval')
    parser.add_argument('--forbidden-tools', nargs='*', default=[], help='tools that must not appear in the draft plan')
    parser.add_argument('--required-source', choices=('llm', 'deterministic'))
    parser.add_argument('--required-arguments-json', type=json.loads, default={},
                        help='JSON object of tool names to required argument subsets, checked before approval')
    args = parser.parse_args()
    raise SystemExit(0 if evaluate(args.base_url.rstrip('/'), args.request, args.output, args.timeout,
                                   args.required_tools, args.forbidden_tools, args.required_source,
                                   args.required_arguments_json) else 1)
