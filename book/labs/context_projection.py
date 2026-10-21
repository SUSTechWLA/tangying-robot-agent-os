#!/usr/bin/env python3
"""Teaching fixture comparing naive history truncation and protected projection.

This is not ProjectManaged or an LLM benchmark. It isolates information-loss
mechanics so students can then inspect the production implementation.
"""
import json
import hashlib


def build(source, max_bytes=2048):
    raw = json.dumps(source, ensure_ascii=False, separators=(',', ':')).encode()
    sha = hashlib.sha256(raw).hexdigest()
    view = {k: source[k] for k in ('scope', 'goal', 'guards', 'unresolved')}
    view.update(completedCount=len(source['completed']), recentCompleted=source['completed'][-3:], archiveSha=sha)
    if len(json.dumps(view, ensure_ascii=False).encode()) > max_bytes:
        raise ValueError('Protected state does not fit; do not discard guards')
    return view, {sha: (source['scope'], raw)}


def read(archive, sha, scope):
    stored_scope, raw = archive[sha]
    if stored_scope != scope or hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('Scope/hash mismatch')
    return json.loads(raw)


def main():
    source = {'scope': 'robot-A/task-P/rev-1', 'goal': 'inspect B and C then return A',
              'guards': ['do not enter D'], 'unresolved': ['command-c12'],
              'completed': [f'c{i}' for i in range(60) if i != 12]}
    naive = source['completed'][-3:]
    view, archive = build(source)
    if read(archive, view['archiveSha'], source['scope']) != source:
        raise SystemExit('Archive lost source information')
    rejected = []
    try:
        read(archive, view['archiveSha'], 'robot-B/task-P/rev-1')
    except ValueError:
        rejected.append('cross_robot_read')
    try:
        build(source, max_bytes=10)
    except ValueError:
        rejected.append('protected_state_over_budget')
    if len(rejected) != 2 or view['unresolved'] != ['command-c12']:
        raise SystemExit('Protected state was not preserved')
    print(json.dumps({'naiveTail': naive, 'protectedView': view, 'rejected': rejected,
                      'archiveRestoredExactly': True, 'physicalExecution': False}, indent=2))


if __name__ == '__main__':
    main()
