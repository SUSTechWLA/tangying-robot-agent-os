"""Robot-side announcement: a robot that has just been switched on says so.

Until this module existed, connecting an agent to a robot began with a human
typing the robot's hostname into a pairing script. Nothing on the robot ever
volunteered its presence. That is the difference between a product a customer can
unbox and one that needs a technician, and it is the gap this closes.

Design constraints, and why they are what they are:

* **The announcement is plaintext and unauthenticated.** It has to be. The two
  ends have no shared secret yet — establishing one is exactly what pairing is
  for. So the payload may contain only what is already visible to anything on the
  same network: an identity, an address, an adapter name, and whether the robot
  is currently accepting a pairing. The pairing code is never announced; doing so
  would make the physical-presence requirement meaningless.
* **Announcing is optional and never blocks serving.** A robot whose network does
  not permit broadcast still accepts a connection at its configured address. This
  thread is a convenience, not a dependency, and a failure in it is reported once
  and then ignored.
* **It says nothing that is not already true.** The pairing state is read from the
  certificate directory at send time, so a robot that has just been paired stops
  claiming it is open to pairing without anyone having to tell it to stop.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The wire contract. These three values are duplicated in the Go listener
# (internal/discovery/announcement.go) and pinned by a fixture both sides are
# tested against (tests/contract/robot_announcement.json), because a protocol
# spoken across a language boundary has no compiler to keep the two honest.
ANNOUNCEMENT_TOPIC = "tangying.robot.announce"
ANNOUNCEMENT_PORT = 45871
PROTOCOL_VERSION = 1

# How often a robot announces. The agent's retention window is expressed in
# multiples of this, so the two cadences stay related if this changes.
ANNOUNCE_INTERVAL_SECONDS = 5.0

# Pairing states. "open" means the robot is accepting a pairing request right
# now; "unpaired" means it has no certificate but is not currently accepting one;
# "paired" means it holds one.
PAIRING_UNPAIRED = "unpaired"
PAIRING_OPEN = "open"
PAIRING_PAIRED = "paired"

MAX_ANNOUNCEMENT_BYTES = 2048


@dataclass(frozen=True)
class RobotIdentity:
    """What the robot knows about itself before it knows about any agent."""

    robot_id: str
    hostname: str
    address: str
    adapter: str
    capability_count: int = 0
    pairing_state: str = PAIRING_UNPAIRED


def resolve_address(listen: str, *, override: str | None = None) -> str:
    """Turn a listen address into an address a *peer* can connect to.

    ``--listen`` defaults to ``0.0.0.0:50051``, which is a perfectly good thing
    to bind and a completely useless thing to announce: a peer told to connect to
    0.0.0.0 would connect to itself. The port is kept and the host is replaced
    with the address of the interface that would actually be used to reach the
    network.
    """
    if override:
        return override
    _, _, port = listen.rpartition(":")
    port = port or "50051"
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent by connect() on UDP; it only selects a route, which
        # is precisely the interface a peer should be told about.
        probe.connect(("192.0.2.1", 9))
        host = probe.getsockname()[0]
    except OSError:
        host = ""
    finally:
        probe.close()
    if not host or host.startswith("127."):
        # Fall back to a hostname lookup before giving up: a machine with no
        # default route may still have a resolvable name, and a name that
        # resolves is worth more to a peer than an address that does not exist.
        try:
            host = socket.gethostbyname(socket.gethostname())
        except OSError:
            host = "127.0.0.1"
    return f"{host}:{port}"


def adapter_environment() -> str:
    """The adapter name is taken from the environment the unit file sets.

    It is deliberately read rather than imported from the backend: the beacon runs
    in its own thread and must not touch hardware, and the backend's own
    description of itself is the authority on capabilities, not this.
    """
    return os.getenv("ROBOT_ADAPTER", "xlerobot") or "xlerobot"


def default_robot_id() -> tuple[str, str]:
    """This robot's identity, and which source it came from.

    Every robot shipped with the same default identity, which is fine for one
    robot on a desk and wrong the moment there are two: discovery keys its list on
    this value, so two robots announcing the same id appear as one robot that
    keeps moving. The identity therefore falls back to something unique to the
    machine.

    ``/etc/machine-id`` is the right fallback rather than a random value or the
    hostname. It is stable across reboots — an identity that changed every power
    cycle would make every restart look like a new robot — and it is unique per
    installed system, which the hostname is not: two robots can both be
    ``raspberrypi``.

    The source is returned alongside the value so the caller can report it. Which
    of the paths was taken is the difference between "this robot has an identity
    someone chose" and "this robot is running on a fallback nobody has looked at",
    and an operator debugging a robot that will not appear needs to know which.
    """
    configured = (os.getenv("ROBOT_ID") or "").strip()
    if configured:
        return configured, "configured"
    try:
        machine_id = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except OSError:
        machine_id = ""
    if machine_id:
        return f"xlerobot-{machine_id[:8]}", "machine-id"
    hostname = (socket.gethostname() or "").strip()
    if hostname:
        return f"xlerobot-{hostname}", "hostname"
    # The historical shared default, kept as a last resort so a machine with
    # neither a machine-id nor a hostname still announces rather than going
    # silent. The caller is expected to say so in the log.
    return "xlerobot-edge-direct", "fallback"


def pairing_state(cert_directory: Path, *, open_for_pairing: bool) -> str:
    """Read the robot's own pairing state off its certificate directory.

    The state is derived, never stored. A stored flag would be a second source of
    truth that can disagree with whether the certificate is actually there — and
    the failure mode of that disagreement is a robot announcing it is paired when
    it cannot accept a connection.
    """
    if (cert_directory / "server.crt").exists() and (cert_directory / "server.key").exists():
        return PAIRING_PAIRED
    if open_for_pairing:
        return PAIRING_OPEN
    return PAIRING_UNPAIRED


def build_announcement(
    identity: RobotIdentity,
    *,
    sent_at: float | None = None,
) -> dict[str, Any]:
    """Build the payload exactly as the Go listener expects to read it."""
    return {
        "topic": ANNOUNCEMENT_TOPIC,
        "version": PROTOCOL_VERSION,
        "robotId": identity.robot_id,
        "hostname": identity.hostname,
        "address": identity.address,
        "adapter": identity.adapter,
        "pairingState": identity.pairing_state,
        "capabilityCount": identity.capability_count,
        "sentAt": _timestamp(sent_at),
    }


def encode_announcement(identity: RobotIdentity, *, sent_at: float | None = None) -> bytes:
    validate(identity)
    payload = json.dumps(
        build_announcement(identity, sent_at=sent_at),
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    if len(payload) > MAX_ANNOUNCEMENT_BYTES:
        # Truncating would produce a payload the reader rejects for a reason that
        # has nothing to do with the actual problem, so this is an error.
        raise ValueError(f"announcement is {len(payload)} bytes, limit is {MAX_ANNOUNCEMENT_BYTES}")
    return payload


def validate(identity: RobotIdentity) -> None:
    """Refuse to announce a robot that cannot be acted on.

    The listener on the other side refuses an announcement with no identity or no
    address, because a robot it cannot show or contact is worse than an absent
    one. That check has to exist here too, and for a stronger reason: a robot that
    broadcasts something the agent silently discards is a robot that looks
    broken to its owner while both ends believe they are working. Failing at the
    point of sending turns that into a log line on the robot.
    """
    if not identity.robot_id.strip():
        raise ValueError("announcement requires a robot id")
    if not identity.address.strip():
        raise ValueError("announcement requires an address")
    if identity.address.startswith("0.0.0.0"):
        # Not a validation nicety: announcing 0.0.0.0 tells the agent to connect
        # to itself, which fails in a way that looks like a network problem.
        raise ValueError(
            "announcement address must be reachable by a peer, not 0.0.0.0; "
            "use resolve_address()"
        )
    if identity.pairing_state not in (PAIRING_UNPAIRED, PAIRING_OPEN, PAIRING_PAIRED):
        raise ValueError(f"unknown pairing state {identity.pairing_state!r}")


def broadcast_targets() -> list[tuple[str, int]]:
    """One broadcast address per broadcast-capable IPv4 interface, plus loopback.

    A robot with both an access-point interface and an uplink has two audiences,
    and the routing table would pick one of them. Announcing on each interface is
    what makes the robot findable by whoever is standing next to it regardless of
    which network they joined.

    Loopback is included, and that is not a debugging convenience. The simulator
    runs the robot runtime and the agent on one laptop, and a robot that only
    announced on the external interface would be invisible to the agent sitting
    next to it in the same process tree — so "power it on and it appears" would
    work on hardware and fail in the demo, which is the wrong way round for the
    first experience anyone has of it.
    """
    targets: list[tuple[str, int]] = [("127.0.0.1", ANNOUNCEMENT_PORT)]
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None  # type: ignore
    if psutil is not None:
        for _name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family != socket.AF_INET or not addr.broadcast:
                    continue
                if addr.address.startswith("127."):
                    continue
                targets.append((addr.broadcast, ANNOUNCEMENT_PORT))
    if not targets:
        targets.append(("255.255.255.255", ANNOUNCEMENT_PORT))
    return targets


@dataclass
class Announcer:
    """Periodically announces this robot until stopped.

    The thread is a daemon and is never joined by the server: a robot that is
    shutting down must not wait on a broadcast socket, and a robot whose
    announcement thread has died must still serve.
    """

    identity_provider: Any
    port: int = ANNOUNCEMENT_PORT
    interval: float = ANNOUNCE_INTERVAL_SECONDS
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _sent: int = field(default=0, repr=False)
    _errors: int = field(default=0, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="robot-announcer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def sent(self) -> int:
        return self._sent

    @property
    def errors(self) -> int:
        return self._errors

    def announce_once(self) -> bool:
        """Send one announcement. Returns whether it reached any interface."""
        identity = self.identity_provider()
        payload = encode_announcement(identity)
        delivered = False
        for host, port in broadcast_targets():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(payload, (host, port if self.port is None else self.port))
                delivered = True
            except OSError:
                continue
            finally:
                sock.close()
        if delivered:
            self._sent += 1
        else:
            self._errors += 1
        return delivered

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.announce_once()
            except Exception:  # noqa: BLE001 - announcing must never take the server down
                self._errors += 1
            self._stop.wait(self.interval)


def _timestamp(value: float | None) -> str:
    """RFC 3339 in UTC, the format Go's time.Time unmarshals without help."""
    seconds = time.time() if value is None else value
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{int(seconds % 1 * 1e9):09d}Z"
