"""Serial live scene regression, with isolated state and explicit coverage.

Starts actual Docker/ROS/Gazebo processes. Reports failed scenes independently,
keeps their logs, and stops only the stack owned by this run. Never uses MuJoCo
as a substitute. Motion probes use a small commissioned joint movement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENES = ('home', 'tabletop', 'home_task', 'home_furnished')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenes', nargs='+', choices=SCENES, default=SCENES)
    parser.add_argument('--sim-port', type=int, default=50191)
    parser.add_argument('--agent-port', type=int, default=8899)
    parser.add_argument('--navigation-port', type=int, default=18891)
    parser.add_argument('--require-full', action='store_true')
    parser.add_argument('--estop', action='store_true', help='Latch each scene journal after testing; use only this isolated namespace')
    parser.add_argument('--business', action='store_true', help='Also execute a full natural-language two-object task and native physics oracle')
    args = parser.parse_args()
    if args.business and args.estop:
        parser.error('--business and --estop require separate runs: a latched runtime cannot execute tasks')
    for value in (args.sim_port, args.agent_port, args.navigation_port):
        if not 1 <= value <= 65535:
            parser.error('ports must be 1..65535')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, SIM_STACK_GAZEBO_NAVIGATION_PORT=str(args.navigation_port),
               SIM_STACK_STARTUP_TIMEOUT=os.environ.get('SIM_STACK_STARTUP_TIMEOUT', '600'))
    env['ROBOT_AGENT_CONFIG_DIR'] = str(output/'isolated-agent-config')
    # Scope all state to this experiment, including inherited shell overrides.
    for name in ('TANGYING_MAP_ROOT', 'TANGYING_HOME_ASSET_PACK', 'TANGYING_SIM_CALIBRATION_DIR',
                 'TANGYING_GAZEBO_WORLD', 'SIM_STACK_SCENE', 'SIM_STACK_PERCEPTION'):
        env.pop(name, None)
    stack = ['bash', str(ROOT/'scripts/sim-stack.sh')]
    options = ['--engine', 'gazebo', '--artifacts-dir', str(output/'stack'),
               '--sim-port', str(args.sim_port), '--agent-port', str(args.agent_port)]
    rows = []
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    try:
        for scene in args.scenes:
            print('Evaluating Gazebo scene:', scene, flush=True)
            with (output/(scene+'-startup.log')).open('w') as log:
                started = subprocess.run([*stack, 'restart', *options, '--scene', scene],
                                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
            row = {'scene': scene, 'startupExit': started.returncode, 'passed': False}
            if started.returncode == 0:
                probe = [sys.executable, str(ROOT/'scripts/evaluate_gazebo_runtime.py'),
                         '--runtime', f'127.0.0.1:{args.sim_port}', '--output', str(output/scene),
                         '--arm-only', '--cancel']
                if args.require_full:
                    probe.append('--require-full')
                if args.estop:
                    probe.append('--estop')
                with (output/(scene+'-probe.log')).open('w') as log:
                    result = subprocess.run(probe, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
                row.update(probeExit=result.returncode, passed=result.returncode == 0)
                if args.business and result.returncode == 0:
                    root = (output/'stack'/'gazebo').resolve()
                    container = 'tangying-gazebo-'+hashlib.sha256(str(root).encode()).hexdigest()[:12]+'-gazebo-house-1'
                    with (output/(scene+'-business.log')).open('w') as log:
                        business = subprocess.run([sys.executable, str(ROOT/'scripts/evaluate_gazebo_business.py'),
                            '--runtime', f'127.0.0.1:{args.sim_port}', '--console', f'http://127.0.0.1:{args.agent_port}',
                            '--case', 'sequence', '--oracle-container', container, '--output', str(output/(scene+'-business'))],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
                    row.update(businessExit=business.returncode, passed=business.returncode == 0)
            rows.append(row)
            report = {'schemaVersion': 'gazebo.scene_matrix.v1', 'baseCommit': revision,
                      'scope': 'startup_cameras_services_joints_cancel'+('_agent_workcell_sequence' if args.business else ''),
                      'navigationRoutesEvaluated': False, 'manipulationTasksEvaluated': args.business,
                      'scenes': rows, 'passed': len(rows) == len(args.scenes) and all(r['passed'] for r in rows)}
            (output/'matrix.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(row), flush=True)
    finally:
        with (output/'shutdown.log').open('w') as log:
            stopped = subprocess.run([*stack, 'stop', *options], cwd=ROOT, env=env,
                                     stdout=log, stderr=subprocess.STDOUT, check=False)
        if stopped.returncode:
            print('Stack cleanup failed; see shutdown.log', file=sys.stderr)
    return 0 if stopped.returncode == 0 and all(row['passed'] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
