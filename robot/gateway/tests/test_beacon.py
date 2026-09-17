"""The announcement a robot broadcasts, and the contract it has to keep.

The agent that listens is written in Go. Nothing in either language's compiler
connects the two, so the wire format is pinned by a fixture — and this file is
where the Python half of that pin lives. If the encoder changes a field name or a
timestamp format, this fails here rather than on a customer's network.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from tangying_robot_gateway import beacon

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "contract" / "robot_announcement.json"

# The identity and timestamp the fixture was generated from. They are constants
# rather than computed values for the obvious reason: a fixture that moves is not
# a contract.
FIXTURE_IDENTITY = beacon.RobotIdentity(
    robot_id="xlerobot-0001",
    hostname="xlerobot.local",
    address="192.168.50.73:50051",
    adapter="xlerobot",
    capability_count=12,
    pairing_state=beacon.PAIRING_OPEN,
    enrollment_port=45872,
)
FIXTURE_SENT_AT = 1758000000.0


def test_the_python_encoder_produces_exactly_the_shared_fixture():
    """The Go reader is tested against this same file.

    Byte equality, not field equality: a reader that tolerates a different
    encoding is a reader that will tolerate a different encoding in production
    too, and the point of the fixture is to remove that tolerance.
    """
    produced = beacon.encode_announcement(FIXTURE_IDENTITY, sent_at=FIXTURE_SENT_AT)
    expected = FIXTURE.read_bytes().strip()
    assert produced == expected, (
        "the robot's announcement no longer matches the contract fixture.\n"
        f"produced: {produced!r}\nexpected: {expected!r}\n"
        "If the change is intended, regenerate tests/contract/robot_announcement.json "
        "and update internal/discovery/discovery_test.go in the same commit."
    )


def test_the_fixture_carries_nothing_that_has_to_stay_secret():
    """An announcement is plaintext on a shared network.

    This is a security property, not a formatting one, so it is asserted rather
    than trusted: anything on the network can read these bytes, and a pairing code
    or a key that leaked into them would make the physical-presence requirement
    pointless.
    """
    payload = json.loads(FIXTURE.read_bytes())
    forbidden = {"pairingCode", "code", "token", "secret", "key", "password", "credential"}
    assert not (forbidden & set(payload)), f"the announcement carries a secret field: {payload}"
    # And nothing that looks like key material, whatever it is called.
    for name, value in payload.items():
        if isinstance(value, str):
            assert "PRIVATE KEY" not in value, f"{name} looks like key material"


def test_every_field_the_go_reader_requires_is_present():
    payload = json.loads(FIXTURE.read_bytes())
    # These are the fields internal/discovery refuses to do without, plus the
    # ones it reads to display a robot. A missing one is a robot that cannot be
    # acted on.
    for field in ("topic", "version", "robotId", "address"):
        assert payload.get(field), f"required field {field} is missing or empty"
    for field in ("hostname", "adapter", "pairingState", "capabilityCount", "sentAt"):
        assert field in payload, f"field {field} is missing"


def test_the_timestamp_is_the_format_the_reader_parses():
    """Go's time.Time is strict about what it accepts, Python's strftime is not.

    This is the single most likely field to drift between the two languages, so
    it is asserted on its own rather than only through the whole-payload fixture.
    """
    payload = json.loads(FIXTURE.read_bytes())
    sent_at = payload["sentAt"]
    assert sent_at.endswith("Z"), sent_at
    assert "T" in sent_at and sent_at.count(":") == 2, sent_at
    # Nine fractional digits: what Python produces and what Go reads without a
    # rounding surprise.
    fraction = sent_at.split(".")[1].rstrip("Z")
    assert len(fraction) == 9 and fraction.isdigit(), sent_at


def test_a_robot_with_no_identity_or_address_is_refused_rather_than_announced():
    """An announcement that cannot be acted on must not be sent.

    A robot in the agent's list with no address is worse than an absent robot: it
    looks like something to connect to and is not.
    """
    with pytest.raises(ValueError):
        beacon.encode_announcement(
            beacon.RobotIdentity(robot_id="r", hostname="h", address="", adapter="a")
        )


def test_a_robot_with_no_id_is_refused():
    with pytest.raises(ValueError):
        beacon.encode_announcement(
            beacon.RobotIdentity(robot_id="", hostname="h", address="1.2.3.4:50051", adapter="a")
        )


def test_an_oversized_announcement_is_refused_not_truncated():
    """Truncation would produce a payload the reader rejects for the wrong reason."""
    with pytest.raises(ValueError):
        beacon.encode_announcement(
            beacon.RobotIdentity(
                robot_id="r", hostname="h" * 4096, address="1.2.3.4:50051", adapter="a"
            )
        )


def test_a_listen_address_of_all_interfaces_is_not_announced_as_all_interfaces():
    """0.0.0.0 is a good thing to bind and a useless thing to tell a peer.

    The peer would be told to connect to itself. Announcing has to substitute the
    address of the interface that actually reaches the network.
    """
    resolved = beacon.resolve_address("0.0.0.0:50051")
    host, _, port = resolved.rpartition(":")
    assert port == "50051", resolved
    assert host != "0.0.0.0", resolved
    assert host, "resolving the local address produced nothing"


def test_the_announced_address_can_be_overridden_for_an_explicit_deployment():
    assert beacon.resolve_address("0.0.0.0:50051", override="10.0.0.5:6000") == "10.0.0.5:6000"


def test_pairing_state_is_read_from_the_certificates_not_stored(tmp_path):
    """The state has to follow the certificates, because they are the truth.

    A stored flag can disagree with whether the certificate is actually there,
    and the failure mode of that disagreement is a robot announcing it is paired
    while being unable to accept a connection.
    """
    certs = tmp_path / "certs"
    certs.mkdir()
    assert beacon.pairing_state(certs, open_for_pairing=False) == beacon.PAIRING_UNPAIRED
    assert beacon.pairing_state(certs, open_for_pairing=True) == beacon.PAIRING_OPEN

    (certs / "server.crt").write_text("certificate")
    (certs / "server.key").write_text("key")
    # Paired wins even while a pairing window is open: a robot that already has a
    # certificate is not waiting to be given one.
    assert beacon.pairing_state(certs, open_for_pairing=True) == beacon.PAIRING_PAIRED


def test_a_half_written_certificate_directory_is_not_reported_as_paired(tmp_path):
    """Half of a certificate pair is not a paired robot.

    Reporting "paired" here would make the agent attempt a mutually authenticated
    connection that the robot cannot complete, and the operator would see a
    connection failure with no explanation.
    """
    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "server.crt").write_text("certificate")
    assert beacon.pairing_state(certs, open_for_pairing=False) == beacon.PAIRING_UNPAIRED


def test_one_announcement_is_sent_per_broadcast_interface(monkeypatch):
    """A robot with two networks announces on both.

    Broadcasting once to a global address would reach whichever network the
    routing table prefers, which is frequently not the one the person standing
    next to the robot is on.
    """
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeSocket:
        def __init__(self, *args, **kwargs):
            pass

        def setsockopt(self, *args):
            pass

        def sendto(self, payload, target):
            sent.append((payload, target))
            return len(payload)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(
        beacon, "broadcast_targets", lambda: [("192.168.50.255", 45871), ("10.0.0.255", 45871)]
    )

    announcer = beacon.Announcer(identity_provider=lambda: FIXTURE_IDENTITY)
    assert announcer.announce_once() is True
    assert [target for _payload, target in sent] == [
        ("192.168.50.255", 45871),
        ("10.0.0.255", 45871),
    ]
    assert announcer.sent == 1
    assert announcer.errors == 0


def test_a_robot_with_no_broadcast_route_counts_the_failure_and_keeps_serving(monkeypatch):
    """Announcing is a convenience; failing to announce must not be fatal."""

    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass

        def setsockopt(self, *args):
            pass

        def sendto(self, payload, target):
            raise OSError("network is unreachable")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FailingSocket)
    monkeypatch.setattr(beacon, "broadcast_targets", lambda: [("192.168.50.255", 45871)])

    announcer = beacon.Announcer(identity_provider=lambda: FIXTURE_IDENTITY)
    assert announcer.announce_once() is False
    assert announcer.errors == 1
    assert announcer.sent == 0


def test_the_pairing_port_is_announced_only_when_a_window_is_open():
    """An agent must not have to assume where the pairing listener is.

    Guessing the default is how an agent reports "the robot is not offering to be
    paired" while the robot is in fact waiting — which is what happened the first
    time the whole flow was run end to end.
    """
    closed = beacon.build_announcement(FIXTURE_IDENTITY.__class__(
        robot_id="r", hostname="h", address="1.2.3.4:50051", adapter="a",
        pairing_state=beacon.PAIRING_UNPAIRED,
    ))
    assert "enrollmentPort" not in closed
    open_window = beacon.build_announcement(FIXTURE_IDENTITY)
    assert open_window["enrollmentPort"] == 45872


def test_loopback_is_announced_on_so_one_machine_deployments_work():
    """The simulator runs the robot and the agent on one laptop.

    A robot that only announced on its external interface would be invisible to the
    agent sitting beside it in the same process tree, so auto-discovery would work
    on hardware and fail in the demo — the wrong way round for a first experience.
    """
    targets = [host for host, _port in beacon.broadcast_targets()]
    assert "127.0.0.1" in targets, targets


def test_the_announced_address_is_never_the_all_interfaces_address():
    """A regression guard for the failure that would make discovery useless."""
    for listen in ("0.0.0.0:50051", "0.0.0.0:6000"):
        assert not beacon.resolve_address(listen).startswith("0.0.0.0:")
