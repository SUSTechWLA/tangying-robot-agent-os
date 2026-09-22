"""Foreground Gazebo owner used by sim-stack's existing PID/rollback supervisor."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'deploy/robot/navigation/gazebo-house.compose.yaml'


def source_revision() -> str:
    digest = hashlib.sha256()
    for area in ('robot/gateway/tangying_robot_gateway', 'robot/ros2_ws/src/tangying_navigation',
                 'robot/ros2_ws/src/tangying_dwb_critics', 'robot/ros2_ws/src/tangying_gazebo_systems', 'python/tangying_robot_proto',
                 'deploy/robot/navigation'):
        for path in sorted((ROOT / area).rglob('*')):
            if path.is_file() and not any(p.startswith('.') or p == '__pycache__' for p in path.relative_to(ROOT).parts):
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


class ProcessOwner:
    """Cancellation covers image builds and asset preparation as well as Compose."""
    def __init__(self):
        self.child = None
        self.stopped = False
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, self.stop)

    def stop(self, _number, _frame):
        self.stopped = True
        if self.child is not None and self.child.poll() is None:
            self.child.send_signal(signal.SIGINT)

    def run(self, arguments, **options):
        if self.stopped:
            raise InterruptedError("Gazebo startup cancelled")
        self.child = subprocess.Popen(arguments, **options)
        if self.stopped:
            self.child.send_signal(signal.SIGINT)
        try:
            result = self.child.wait()
        finally:
            self.child = None
        if self.stopped:
            raise InterruptedError("Gazebo process stopped")
        if result:
            raise subprocess.CalledProcessError(result, arguments)
        return result


def ensure_image(owner: ProcessOwner, image: str) -> None:
    revision = source_revision()
    inspection = subprocess.run(['docker', 'image', 'inspect', image, '--format',
                                 '{{index .Config.Labels "org.tangying.source.sha256"}}'],
                                capture_output=True, text=True, check=False)
    if inspection.returncode or inspection.stdout.strip() != revision:
        print('Building Gazebo runtime for the current source revision', flush=True)
        owner.run(['docker', 'build', '-t', image, '--label',
                   'org.tangying.source.sha256=' + revision, '-f',
                   str(ROOT / 'deploy/robot/navigation/Dockerfile'), str(ROOT)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--listen', required=True)
    parser.add_argument('--scene', choices=('tabletop', 'home', 'home_task', 'home_furnished'), required=True)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--artifacts-dir', type=Path, required=True)
    args = parser.parse_args()
    host, port = args.listen.rsplit(':', 1)
    if host != '127.0.0.1' or not 1 <= int(port) <= 65535:
        parser.error('runtime must bind an explicit local port')
    owner = ProcessOwner()
    subprocess.run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL)
    root = args.artifacts_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    project = 'tangying-gazebo-' + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    token_path = root / 'navigation-token'
    if not token_path.exists():
        with open(token_path, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
            handle.write(secrets.token_urlsafe(32))
    image = os.environ.get('SIM_STACK_GAZEBO_IMAGE', 'tangying-navigation:dev')
    ensure_image(owner, image)
    assets = ROOT / 'artifacts/sim-assets'
    assets.mkdir(parents=True, exist_ok=True)
    if args.scene == 'home_furnished' and not (assets / 'aws-small-house-harmonic.sdf').is_file():
        owner.run([os.sys.executable, str(ROOT / 'scripts/prepare_home_world.py'),
                        '--output', str(assets)])
    port_file = root / 'navigation-port'
    navigation_port = os.environ.get('SIM_STACK_GAZEBO_NAVIGATION_PORT') or (port_file.read_text().strip() if port_file.exists() else '18791')
    if not navigation_port.isdigit() or not 1 <= int(navigation_port) <= 65535:
        parser.error('navigation port must be between 1 and 65535')
    port_file.write_text(navigation_port + '\n')
    env = dict(os.environ, TANGYING_NAVIGATION_IMAGE=image,
               TANGYING_GAZEBO_RUNTIME_PORT=port,
               TANGYING_NAVIGATION_PORT=navigation_port,
               TANGYING_NAVIGATION_TOKEN=token_path.read_text().strip(),
               TANGYING_GAZEBO_ASSETS=str(assets.resolve()))
    (root / 'maps').mkdir(exist_ok=True)
    host_map_root = Path(os.environ.get('TANGYING_MAP_ROOT') or root / 'maps' / args.scene / 'workflow').resolve()
    host_map_root.mkdir(parents=True, exist_ok=True)
    # Each scene has separate persisted maps and journals; no map silently changes worlds.
    override = root / 'compose.json'
    override.write_text(json.dumps({'services': {'gazebo-house': {'stop_grace_period': '10s',
        'volumes': [str(root / 'maps') + ':/data/maps', str(host_map_root) + ':/data/maps/' + args.scene + '/workflow'],
        'environment': {
        'TANGYING_GAZEBO_SCENE': args.scene,
        # GzServer does not expose a physics seed; this is only an episode identifier.
        'TANGYING_GAZEBO_EPISODE_SEED': str(args.seed),
        'TANGYING_RUNTIME_ROBOT_ID': 'gazebo-' + args.scene,
        'TANGYING_NAVIGATION_GOAL_DATABASE': '/data/maps/' + args.scene + '/navigation.sqlite',
        'TANGYING_GAZEBO_MAP_NAMESPACE': '/data/maps/' + args.scene,
        'TANGYING_MAP_ROOT': '/data/maps/' + args.scene + '/workflow',
        'TANGYING_GAZEBO_RUNTIME_ROOT': '/data/maps/' + args.scene + '/runtime',
    }}}}, indent=2) + '\n')
    command = ['docker', 'compose', '-p', project, '-f', str(COMPOSE), '-f', str(override)]
    try:
        return owner.run([*command, 'up', '--abort-on-container-exit',
                          '--exit-code-from', 'gazebo-house'], env=env)
    finally:
        subprocess.run([*command, 'down', '--timeout', '10'], env=env, timeout=30, check=True)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except InterruptedError:
        raise SystemExit(0) from None
