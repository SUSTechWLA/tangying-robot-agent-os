"""Robot-side pairing: the protocol, the window, and the way it fails.

The agent that pairs with this robot is written in Go, so the wire format is
pinned by a fixture rather than by hoping. This file holds one half of that pin,
and the other half is internal/pairing/contract_test.go.
"""

from __future__ import annotations

import base64
import json
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from tangying_robot_gateway import pairing

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "contract" / "robot_pairing.json"

# The inputs the fixture was generated from. Constants rather than computed
# values, for the obvious reason: a fixture that moves is not a contract.
FIXTURE_CODE = "4F2K-9QW7"
FIXTURE_ROBOT = "xlerobot-0001"
FIXTURE_SALT = bytes(range(32))
FIXTURE_NONCE = bytes(range(12))
FIXTURE_ANSWER_NONCE = bytes(range(100, 112))
FIXTURE_MATERIAL = {
    "ca": "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n",
    "serverCert": "-----BEGIN CERTIFICATE-----\nSERVER\n-----END CERTIFICATE-----\n",
    "serverKey": "-----BEGIN EC PRIVATE KEY-----\nKEY\n-----END EC PRIVATE KEY-----\n",
}


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- the contract with the Go side -----------------------------------------


def test_the_encoder_reproduces_the_shared_fixture_exactly():
    """Byte equality, not field equality.

    A reader that tolerates a different encoding tolerates a different encoding in
    production too, and removing that tolerance is the entire point of a fixture.
    """
    fixture = load_fixture()
    produced = json.loads(
        pairing.encode_request(
            FIXTURE_CODE, FIXTURE_MATERIAL, FIXTURE_ROBOT,
            salt=FIXTURE_SALT, nonce=FIXTURE_NONCE,
        )
    )
    assert produced == fixture["request"], (
        "the robot's pairing request no longer matches the contract fixture.\n"
        "If the change is intended, regenerate tests/contract/robot_pairing.json and "
        "update internal/pairing/contract_test.go in the same commit."
    )
    answer = json.loads(
        pairing.encode_result(
            FIXTURE_CODE, FIXTURE_SALT, FIXTURE_ANSWER_NONCE, FIXTURE_ROBOT, {"status": "paired"}
        )
    )
    assert answer == fixture["answer"]


def test_the_derived_key_matches_the_go_side():
    """The one value both implementations must agree on above all others.

    A key-derivation function that differs by one byte produces a pairing that
    fails with no explanation, so this is asserted against the recorded value
    rather than only through the ciphertext.
    """
    fixture = load_fixture()
    assert base64.b64encode(pairing.derive_key(FIXTURE_CODE, FIXTURE_SALT)).decode() == fixture["derivedKey"]


def test_the_go_sides_fixture_material_round_trips_here():
    fixture = load_fixture()
    request = json.dumps(fixture["request"]).encode()
    material, parsed = pairing.decode_request(FIXTURE_CODE, request)
    assert material == FIXTURE_MATERIAL
    assert parsed["robotId"] == FIXTURE_ROBOT


# --- the code is read by a person ------------------------------------------


def test_a_code_is_generated_in_the_grouped_form_a_person_reads():
    code = pairing.generate_code()
    assert len(code) == 9 and code[4] == "-", code
    # The ambiguous characters are excluded because this is read off a label and
    # typed; a pairing that fails because of a font is a support call.
    assert not set(code) & set("OI01"), code


def test_the_same_code_written_differently_still_works():
    for variant in ("4F2K-9QW7", "4f2k9qw7", " 4F2K 9QW7 ", "4f2k_9qw7"):
        assert pairing.normalize_code(variant) == "4F2K9QW7"


def test_an_empty_code_never_authorises_anything():
    """A robot with no code configured must mean "no pairing", not "any pairing"."""
    assert pairing.codes_equal("", "") is False
    assert pairing.codes_equal("---", "") is False
    with pytest.raises(pairing.PairingError):
        pairing.derive_key("", FIXTURE_SALT)
    with pytest.raises(pairing.PairingError):
        pairing.derive_key("---", FIXTURE_SALT)


def test_codes_are_generated_fresh_so_two_robots_do_not_share_one():
    codes = {pairing.generate_code() for _ in range(50)}
    assert len(codes) == 50, "two robots were issued the same pairing code"


# --- the window -------------------------------------------------------------


def unpaired_robot(tmp_path: Path) -> tuple[pairing.PairingState, Path, list[tuple[str, dict]]]:
    certificate_directory = tmp_path / "certs"
    state = pairing.PairingState(directory=tmp_path / "state")
    events: list[tuple[str, dict]] = []
    return state, certificate_directory, events


def test_the_window_does_not_open_on_an_already_paired_robot(tmp_path):
    """An enrollment listener on a paired robot is a way to replace its certificate.

    Without anyone touching it, over the network. So it is refused rather than
    silently skipped, and the refusal is reported.
    """
    state, certificate_directory, events = unpaired_robot(tmp_path)
    certificate_directory.mkdir(parents=True)
    (certificate_directory / "server.crt").write_text("already paired")

    server = pairing.EnrollmentServer(
        state=state, certificate_directory=certificate_directory, robot_id=FIXTURE_ROBOT,
        port=0, on_event=lambda event, detail: events.append((event, detail)),
    )
    assert server.start() is False
    assert [event for event, _ in events] == ["pairing.skipped"]
    assert server.open is False


def test_a_pairing_installs_the_material_with_the_same_modes_as_the_ssh_path(tmp_path):
    """Same three files, same directory, same modes.

    A robot paired over the network must be byte-identical to one paired by
    scripts/pair-robot.sh, or the two paths drift into being two kinds of pairing.
    """
    state, certificate_directory, events = unpaired_robot(tmp_path)
    server = pairing.EnrollmentServer(
        state=state, certificate_directory=certificate_directory, robot_id=FIXTURE_ROBOT,
        port=0, on_event=lambda event, detail: events.append((event, detail)),
    )
    code = state.load()
    assert server.start() is True
    try:
        answer = pair_over_socket(server.bound_port, code, FIXTURE_ROBOT)
        assert answer["status"] == "paired"
    finally:
        server.stop()

    assert (certificate_directory / "server.key").read_text() == FIXTURE_MATERIAL["serverKey"]
    assert (certificate_directory / "server.crt").read_text() == FIXTURE_MATERIAL["serverCert"]
    assert (certificate_directory / "client-ca.crt").read_text() == FIXTURE_MATERIAL["ca"]
    assert (certificate_directory / "server.key").stat().st_mode & 0o777 == 0o600
    assert (certificate_directory / "server.crt").stat().st_mode & 0o777 == 0o644
    # No half-written file is left behind for a reader to find.
    assert list(certificate_directory.glob("*.tmp")) == []
    assert [event for event, _ in events] == ["pairing.window-open", "pairing.paired"]


def test_a_successful_pairing_retires_the_code(tmp_path):
    """A code that outlives its pairing is a permanent key to the robot."""
    state, certificate_directory, _events = unpaired_robot(tmp_path)
    server = pairing.EnrollmentServer(
        state=state, certificate_directory=certificate_directory, robot_id=FIXTURE_ROBOT, port=0,
    )
    state.load()
    assert server.start() is True
    try:
        pair_over_socket(server.bound_port, state.code, FIXTURE_ROBOT)
        # The reply precedes the retirement, so the assertion below has to wait
        # for the effect instead of racing the thread that produces it.
        wait_for_retired_code(state)
    finally:
        server.stop()

    assert state.paired is True
    assert state.code == ""
    assert not (state.directory / pairing.PairingState.CODE_FILE).exists()


def test_a_wrong_code_is_refused_and_installs_nothing(tmp_path):
    state, certificate_directory, events = unpaired_robot(tmp_path)
    server = pairing.EnrollmentServer(
        state=state, certificate_directory=certificate_directory, robot_id=FIXTURE_ROBOT,
        port=0, on_event=lambda event, detail: events.append((event, detail)),
    )
    state.load()
    assert server.start() is True
    try:
        with pytest.raises(pairing.PairingError):
            pair_over_socket(server.bound_port, "AAAA-BBBB", FIXTURE_ROBOT)
    finally:
        server.stop()

    assert not (certificate_directory / "server.key").exists()
    rejected = [detail for event, detail in events if event == "pairing.rejected"]
    assert len(rejected) == 1
    # The peer is named, because a pairing window is exactly when someone would
    # try, and an owner has to be able to see an attempt that was not theirs.
    assert "peer" in rejected[0]


def test_the_window_shuts_after_too_many_wrong_codes(tmp_path):
    """The code is short enough to read aloud, so guesses must not be free."""
    state, certificate_directory, events = unpaired_robot(tmp_path)
    server = pairing.EnrollmentServer(
        state=state, certificate_directory=certificate_directory, robot_id=FIXTURE_ROBOT,
        port=0, max_attempts=3, on_event=lambda event, detail: events.append((event, detail)),
    )
    state.load()
    assert server.start() is True
    for _ in range(3):
        with pytest.raises(pairing.PairingError):
            pair_over_socket(server.bound_port, "AAAA-BBBB", FIXTURE_ROBOT)

    assert any(event == "pairing.window-closed" for event, _ in events)
    assert any(detail.get("reason") == "too-many-attempts" for _, detail in events)
    assert server.open is False


def test_regenerating_the_code_is_how_a_lost_code_is_recovered(tmp_path):
    """Re-opening pairing requires local access, which is the point."""
    state = pairing.PairingState(directory=tmp_path / "state")
    first = state.load()
    assert (tmp_path / "state" / pairing.PairingState.CODE_FILE).exists()
    second = state.regenerate()
    assert second != first
    assert state.attempts == 0


def test_the_window_expires_on_its_own(tmp_path):
    """A window that never closed would leave an unpaired robot permanently claimable."""
    state, certificate_directory, events = unpaired_robot(tmp_path)
    server = pairing.EnrollmentServer(
        state=state, certificate_directory=certificate_directory, robot_id=FIXTURE_ROBOT,
        port=0, window_seconds=0.5, on_event=lambda event, detail: events.append((event, detail)),
    )
    state.load()
    assert server.start() is True
    assert server.open is True
    # Waited on the report rather than on the flag: `open` turns false the moment
    # the clock passes, while the serving thread reports the closure a moment
    # later, and asserting between the two is a race, not a test.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        event == "pairing.window-closed" for event, _ in events
    ):
        time.sleep(0.05)
    assert any(detail.get("reason") == "expired" for event, detail in events if event == "pairing.window-closed")
    assert server.open is False
    server.stop()


def test_a_request_for_another_robot_is_refused(tmp_path):
    """The material is bound to a robot id, so a request aimed elsewhere cannot land."""
    state, certificate_directory, events = unpaired_robot(tmp_path)
    server = pairing.EnrollmentServer(
        state=state, certificate_directory=certificate_directory, robot_id="xlerobot-0009",
        port=0, on_event=lambda event, detail: events.append((event, detail)),
    )
    code = state.load()
    assert server.start() is True
    try:
        # Sealed for a different robot: it will not open at all, which is the
        # stronger refusal.
        with pytest.raises(pairing.PairingError):
            pair_over_socket(server.bound_port, code, "xlerobot-0001")
    finally:
        server.stop()
    assert not (certificate_directory / "server.key").exists()


# --- helpers ----------------------------------------------------------------


def pair_over_socket(port: int, code: str, robot_id: str, material: dict | None = None) -> dict:
    """Run one real pairing attempt over a real socket.

    Over a socket rather than by calling the handler, because the framing and the
    timeout behaviour are part of the protocol and a test that skips them would
    not notice them breaking.
    """
    request = pairing.encode_request(code, material or FIXTURE_MATERIAL, robot_id)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.settimeout(5)
        reader = connection.makefile("rb")
        writer = connection.makefile("wb")
        pairing.write_frame(writer, request)
        writer.flush()
        try:
            answer = pairing.read_frame(reader)
        except pairing.PairingError:
            raise pairing.PairingError("机器人没有应答")
    salt = base64.b64decode(json.loads(request)["salt"])
    return pairing.decode_result(code, salt, robot_id, answer)


def wait_for_retired_code(state: pairing.PairingState, *, timeout: float = 5.0) -> None:
    """Block until the served code has been retired, or fail saying it never was.

    The server answers the socket *before* it retires the code, so a client that
    asserts the retirement the moment its reply arrives is racing the thread that
    is about to perform it. That race made this file fail roughly one run in five,
    with the code still on disk - which reads exactly like the defect the assertion
    exists to catch, and cost more to disbelieve than to wait out.

    Waiting on the effect rather than on an event keeps the assertion about the
    thing that matters: ``pairing.paired`` is reported before ``consume()`` runs,
    so an event would be signalled in the window this is closing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state.code == "" and not (state.directory / pairing.PairingState.CODE_FILE).exists():
            return
        time.sleep(0.005)
    raise AssertionError(
        "the code was still being served 5 s after the robot answered the pairing: "
        "a code that outlives its pairing is a permanent key to the robot")


def test_enrollment_state_is_never_written_to_the_working_directory():
    """A relative certificate path must not put the pairing code in the CWD.

    During a test run this produced `./pairing/pairing-code` at the root of the
    repository: the enrollment state belongs next to the certificates, or nowhere.
    """
    from tangying_robot_gateway import run_direct_edge

    for unknown in ("", ".", "/"):
        assert run_direct_edge._start_enrollment(Path(unknown), "xlerobot-0001") is None


# --- what happens after pairing ---------------------------------------------


def test_a_paired_robot_refuses_to_serve_in_plaintext(tmp_path, monkeypatch):
    """The half that makes "paired" mean something.

    A pairing installs a certificate; the point of the certificate is that the
    robot stops accepting plaintext connections and starts requiring a client
    certificate. If it went on serving plaintext, the pairing would have bought
    nothing at all.
    """
    from tangying_robot_gateway import service as gateway_service

    started = {}

    class FakeServer:
        def add_insecure_port(self, *_args):
            started["plaintext"] = True
            return 1

        def add_secure_port(self, *_args):
            started["secure"] = True
            return 1

        def start(self):
            started["started"] = True

    monkeypatch.setattr(gateway_service.grpc, "server", lambda *_a, **_k: FakeServer())
    monkeypatch.setattr(gateway_service.robot_pb2_grpc, "add_RobotRuntimeServicer_to_server",
                        lambda *_a, **_k: None)

    # A backend that can describe itself and nothing else: this test is about the
    # transport, and a fake with hardware would be a second thing to get wrong.
    backend = SimpleNamespace(capabilities=lambda: SimpleNamespace(
        robot_id="xlerobot-test", adapter="xlerobot", adapter_version="v1",
        capabilities=[], robot_profile=None,
    ))

    # No credentials and no explicit insecure opt-in: refused outright rather
    # than silently falling back to plaintext.
    with pytest.raises(ValueError, match="mTLS credentials are required"):
        gateway_service.start_server(backend, "127.0.0.1:0")
    assert started == {}

    # With credentials it uses them, and it requires the client's certificate —
    # the mutual half of mutual TLS.
    certificate_directory = tmp_path / "certs"
    certificate_directory.mkdir()
    for name, content in (("server.key", "key"), ("server.crt", "cert"), ("client-ca.crt", "ca")):
        (certificate_directory / name).write_text(content)
    credentials = {}
    monkeypatch.setattr(gateway_service.grpc, "ssl_server_credentials",
                        lambda pairs, **kwargs: credentials.update(kwargs) or "creds")
    gateway_service.start_server(
        backend, "127.0.0.1:0",
        server_key=certificate_directory / "server.key",
        server_cert=certificate_directory / "server.crt",
        client_ca=certificate_directory / "client-ca.crt",
    )
    assert started.get("secure") is True
    assert "plaintext" not in started
    assert credentials.get("require_client_auth") is True


def test_a_paired_robot_does_not_reopen_its_pairing_window(tmp_path):
    """After pairing, the window stays shut on the next start.

    An enrollment listener on a paired robot is a way to replace its certificate
    over the network without anyone touching it.
    """
    from tangying_robot_gateway import run_direct_edge

    certificate_directory = tmp_path / "certs"
    certificate_directory.mkdir()
    (certificate_directory / "server.crt").write_text("certificate")
    (certificate_directory / "server.key").write_text("key")

    assert run_direct_edge._start_enrollment(certificate_directory, "xlerobot-0001") is None
