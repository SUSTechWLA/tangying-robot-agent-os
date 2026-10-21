#!/usr/bin/env python3
"""Teaching fixture: lost replies, repeated effects, and bound receipt recovery.

No ROS, network, model, or robot access. device.json is a simulated physical
counter, deliberately separate from the controller's SQLite transaction.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def run_case(path: Path, strategy: str, receipt: bool):
    path.mkdir()
    db_path = path / 'controller.sqlite'
    device_path = path / 'device.json'
    device_path.write_text(json.dumps({'effects': 0, 'receipt': None}))
    events = []
    def record(event, **data):
        events.append({'event': event, **data})
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE command (id TEXT PRIMARY KEY, state TEXT)')
        db.execute('INSERT INTO command VALUES (?, ?)', ('c1', 'PENDING'))
    record('pending_committed', command='c1')
    # Device has changed, but the controller has not received a reply.
    device_path.write_text(json.dumps({'effects': 1, 'receipt': 'c1' if receipt else None}))
    record('effect_happened_reply_lost', effects=1)
    # A fresh connection represents controller reconstruction after a crash.
    with sqlite3.connect(db_path) as db:
        state = db.execute('SELECT state FROM command WHERE id=?', ('c1',)).fetchone()[0]
        device = json.loads(device_path.read_text())
        if strategy == 'naive':
            device['effects'] += 1
            device_path.write_text(json.dumps(device))
            state = 'COMPLETED'
            record('blind_retry', effects=device['effects'])
        elif strategy == 'reconcile' and device['receipt'] == 'c1':
            state = 'COMPLETED'
            record('bound_receipt_reconciled', command='c1')
        else:
            state = 'UNKNOWN'
            record('no_replay_without_bound_evidence')
        db.execute('UPDATE command SET state=? WHERE id=?', (state, 'c1'))
    result = {'strategy': strategy, 'effects': device['effects'], 'state': state, 'events': events}
    (path / 'trace.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new, unused output directory')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = [run_case(args.output/'naive', 'naive', False),
               run_case(args.output/'guarded', 'guarded', False),
               run_case(args.output/'reconciled', 'reconcile', True)]
    expected = [(2, 'COMPLETED'), (1, 'UNKNOWN'), (1, 'COMPLETED')]
    actual = [(r['effects'], r['state']) for r in results]
    if actual != expected:
        raise SystemExit(f'Unexpected fixture outcomes: {actual}')
    report = {'physicalExecution': False, 'results': results,
              'assumption': 'A simulated matching device receipt is trusted in case 3. Real robot recovery additionally validates full binding, authority and observation.'}
    (args.output/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    for r in results:
        print(f"{r['strategy']}: effects={r['effects']}, controller={r['state']}")


if __name__ == '__main__':
    main()
