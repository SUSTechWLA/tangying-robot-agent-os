"""Robot-side pairing: how a robot joins an agent without SSH.

Until this module existed, the only way to install a certificate on a robot was
``scripts/pair-robot.sh``, which needs the robot's hostname, an SSH account,
key-based authentication and ``sudo`` on the far side. That is a technician's
workflow. This is the same delivery over the network, gated by a code the robot
prints and a person reads.

The problem that shapes everything here
--------------------------------------

The two ends share no secret, and you cannot authenticate a device you share no
secret with. So:

* the robot prints a one-time pairing code; it is the only pre-shared value;
* the code is used as a pre-shared key through HKDF-SHA256, and the certificate
  material travels sealed under AES-256-GCM — so an eavesdropper learns nothing
  and an impostor cannot answer;
* the code is single-use and expires, because a pairing code that outlives its
  pairing is a permanent key to the robot.

What is deliberately not done
-----------------------------

No cryptographic primitive is invented. HKDF and AES-GCM come from
``cryptography``, and the implementation here is checked against the Go side's
through a shared fixture plus the RFC 5869 test vectors on both sides — because
hand-written key derivation is exactly the code that is wrong in a way nothing
notices.

The threat model, stated plainly
-------------------------------

* A passive eavesdropper learns nothing: the payload is encrypted.
* An active impostor cannot answer: it does not know the code.
* Someone who *does* know the code can pair, and is meant to be able to. The code
  is what says "I am standing at this robot", which is why it is printed by the
  robot and never broadcast.
* An attacker who can read the code and race the owner wins. The mitigations are
  the ones that fit: the code is single-use, it expires, and every attempt is
  reported.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import socket
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENROLL_TOPIC = "tangying.robot.enroll"
RESULT_TOPIC = "tangying.robot.enroll.result"
ENROLL_VERSION = 1
ENROLL_PORT = 45872
MAX_ENROLL_MESSAGE = 64 * 1024

# Separates this key from any other key derived from the same code, so a future
# protocol cannot accidentally reuse this one's key stream.
KEY_INFO = b"tangying.robot.enroll.v1"
KEY_LENGTH = 32

# How long a pairing window stays open after an unpaired robot starts.
#
# Fifteen minutes is the shape of the act: someone unboxes a robot, powers it on,
# and walks to their laptop. A window that closed in a minute would be a race; one
# that never closed would leave an unpaired robot permanently willing to be
# claimed by whoever reads the code first.
DEFAULT_WINDOW_SECONDS = 900.0

# How many wrong codes one window tolerates before it shuts.
#
# The code is short enough to read aloud, so it is short enough to guess at if
# guesses are free. Five is enough for a typo and not enough for a search.
MAX_ATTEMPTS = 5

# Ambiguous characters are excluded: this code is read off a label or a log by a
# person and typed. O/0 and I/1 are the pairs that get mistyped, and a pairing that
# fails because of a font is a support call.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUPS = 2
CODE_GROUP_SIZE = 4


class PairingError(Exception):
    """A pairing that did not happen, with a sentence for the operator."""


def generate_code() -> str:
    """A fresh pairing code, in the grouped form a person reads.

    Eight characters from a 32-symbol alphabet is 40 bits. That is not a
    cryptographic key on its own — it does not need to be, because it is
    single-use, expires, and is limited to a handful of attempts. It does need to
    be long enough that guessing inside one window is hopeless, which 40 bits is.
    """
    symbols = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUPS * CODE_GROUP_SIZE))
    return "-".join(
        symbols[index : index + CODE_GROUP_SIZE]
        for index in range(0, len(symbols), CODE_GROUP_SIZE)
    )


def normalize_code(code: str) -> str:
    """The one form both ends hash.

    The code is read off a label or a log and typed, so the same code arrives as
    ``4F2K-9QW7`` or ``4f2k9qw7`` or with a space in it. Normalising here rather
    than at the comparison means a correct code is never refused for looking
    different — and it is done identically on the Go side, which is why the two
    are checked against one fixture instead of against each other's intentions.
    """
    return "".join(symbol for symbol in code.strip().upper() if symbol not in "-_ \t")


def codes_equal(left: str, right: str) -> bool:
    """Compare two codes without leaking where they differ.

    An empty code matches nothing, including another empty code: a robot with no
    code configured must mean "no pairing is possible", never "any pairing".
    """
    normalized_left, normalized_right = normalize_code(left), normalize_code(right)
    if not normalized_left or not normalized_right:
        return False
    return hmac.compare_digest(normalized_left, normalized_right)


def derive_key(code: str, salt: bytes) -> bytes:
    normalized = normalize_code(code)
    if not normalized:
        raise PairingError("配对码为空")
    return HKDF(
        algorithm=SHA256(), length=KEY_LENGTH, salt=salt, info=KEY_INFO
    ).derive(normalized.encode("utf-8"))


def seal(code: str, salt: bytes, nonce: bytes, robot_id: str, plaintext: bytes) -> str:
    """Encrypt one payload, bound to a robot.

    The robot id is additional authenticated data. Without it, a request captured
    while pairing robot A could be replayed at robot B by anyone who had also
    learned B's code, and "the code is per robot" would be a claim the
    cryptography did not actually make.
    """
    sealed = AESGCM(derive_key(code, salt)).encrypt(nonce, plaintext, robot_id.encode("utf-8"))
    return base64.b64encode(sealed).decode("ascii")


def open_sealed(code: str, salt: bytes, nonce: bytes, robot_id: str, payload: str) -> bytes:
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise PairingError("请求不是有效的 base64") from exc
    try:
        return AESGCM(derive_key(code, salt)).decrypt(nonce, raw, robot_id.encode("utf-8"))
    except Exception as exc:
        raise PairingError("配对码不正确，或请求被篡改") from exc


def encode_request(code: str, material: dict, robot_id: str, *,
                   salt: bytes | None = None, nonce: bytes | None = None) -> bytes:
    salt = salt or secrets.token_bytes(32)
    nonce = nonce or secrets.token_bytes(12)
    payload = seal(code, salt, nonce, robot_id, json.dumps(material).encode("utf-8"))
    return json.dumps(
        {
            "topic": ENROLL_TOPIC,
            "version": ENROLL_VERSION,
            "robotId": robot_id,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "payload": payload,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_request(code: str, data: bytes) -> tuple[dict, dict]:
    """Read a request and decrypt its material. Raises PairingError on refusal."""
    if not data or len(data) > MAX_ENROLL_MESSAGE:
        raise PairingError("请求大小不合法")
    try:
        request = json.loads(data)
    except Exception as exc:
        raise PairingError("请求不是 JSON") from exc
    if request.get("topic") != ENROLL_TOPIC:
        raise PairingError("这不是配对请求")
    if request.get("version") != ENROLL_VERSION:
        raise PairingError(
            f"配对协议版本不一致：机器人支持 {ENROLL_VERSION}，请求是 {request.get('version')}"
        )
    robot_id = str(request.get("robotId") or "")
    if not robot_id.strip():
        raise PairingError("配对请求没有机器人身份")
    try:
        salt = base64.b64decode(request.get("salt", ""), validate=True)
        nonce = base64.b64decode(request.get("nonce", ""), validate=True)
    except Exception as exc:
        raise PairingError("请求的 salt/nonce 不合法") from exc
    if len(nonce) != 12:
        raise PairingError("请求的 nonce 长度不合法")
    material = json.loads(
        open_sealed(code, salt, nonce, robot_id, request.get("payload", "")).decode("utf-8")
    )
    for key in ("ca", "serverCert", "serverKey"):
        if not str(material.get(key) or "").strip():
            raise PairingError(f"配对材料缺少 {key}")
    return material, request


def encode_result(code: str, salt: bytes, nonce: bytes, robot_id: str, body: dict) -> bytes:
    payload = seal(code, salt, nonce, robot_id, json.dumps(body).encode("utf-8"))
    return json.dumps(
        {
            "topic": RESULT_TOPIC,
            "version": ENROLL_VERSION,
            "robotId": robot_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "payload": payload,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_result(code: str, salt: bytes, robot_id: str, data: bytes) -> dict:
    try:
        result = json.loads(data)
    except Exception as exc:
        raise PairingError("应答不是 JSON") from exc
    if result.get("topic") != RESULT_TOPIC:
        raise PairingError("这不是配对应答")
    nonce = base64.b64decode(result.get("nonce", ""), validate=True)
    return json.loads(
        open_sealed(code, salt, nonce, robot_id, result.get("payload", "")).decode("utf-8")
    )


def write_frame(writer, message: bytes) -> None:
    """Write one length-prefixed message.

    A length prefix rather than a stream terminator, because the payload is base64
    JSON that may legitimately contain any byte a terminator would use.
    """
    if len(message) > MAX_ENROLL_MESSAGE:
        raise PairingError(f"报文 {len(message)} 字节，超过上限 {MAX_ENROLL_MESSAGE}")
    writer.write(struct.pack(">I", len(message)))
    writer.write(message)


def read_frame(reader) -> bytes:
    header = reader.read(4)
    if len(header) != 4:
        raise PairingError("连接在报文头之前就结束了")
    (size,) = struct.unpack(">I", header)
    if size == 0 or size > MAX_ENROLL_MESSAGE:
        raise PairingError(f"报文长度 {size} 不合法")
    body = reader.read(size)
    if len(body) != size:
        raise PairingError("连接在报文读完之前就结束了")
    return body


@dataclass
class PairingState:
    """The robot's pairing code and whether a window is open.

    The code lives in a file next to the certificates, readable only by the robot
    user. It is generated on first need and never announced: broadcasting it would
    make "someone has to be standing at the robot" meaningless.
    """

    directory: Path
    code: str = ""
    attempts: int = 0
    paired: bool = False

    CODE_FILE = "pairing-code"

    def load(self) -> str:
        path = self.directory / self.CODE_FILE
        if path.exists():
            self.code = path.read_text(encoding="utf-8").strip()
            return self.code
        self.code = generate_code()
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(self.code + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            # A filesystem that refuses the mode is not a reason to refuse to pair;
            # the file is still inside a 0700 state directory.
            pass
        return self.code

    def regenerate(self) -> str:
        """Issue a fresh code and reset the attempt budget.

        This is the recovery path for a lost or leaked code, and for the window
        that shut after too many wrong guesses. It requires local access to the
        robot, which is the point: re-opening pairing is a physical act.
        """
        path = self.directory / self.CODE_FILE
        if path.exists():
            path.unlink()
        self.code = ""
        self.attempts = 0
        self.paired = False
        return self.load()

    def consume(self) -> None:
        """Retire the code after a successful pairing.

        A pairing code that survives its own pairing is a permanent key to the
        robot. Retiring it is the whole reason a one-time code is one-time.
        """
        self.paired = True
        path = self.directory / self.CODE_FILE
        if path.exists():
            path.unlink()
        self.code = ""


@dataclass
class EnrollmentServer:
    """The one-time listener an unpaired robot runs.

    It is open only while the robot is unpaired and a window is open, and it hands
    nothing over until the request proves it knows the code. Every refusal is
    reported through ``on_event`` so an owner can see an attempt that was not
    theirs — a pairing window is exactly when someone would try.
    """

    state: PairingState
    certificate_directory: Path
    robot_id: str
    port: int = ENROLL_PORT
    window_seconds: float = DEFAULT_WINDOW_SECONDS
    max_attempts: int = MAX_ATTEMPTS
    on_event: Callable[[str, dict], None] | None = None
    _server: socket.socket | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _deadline: float = field(default=0.0, repr=False)
    _bound_port: int = field(default=0, repr=False)

    @property
    def bound_port(self) -> int:
        """The port actually listening.

        It differs from ``port`` when ``port`` is 0, which asks the operating
        system to choose. Tests use that so two of them cannot collide on one
        fixed port — a collision that looks like "pairing does not work" and is
        really "something else was still listening".
        """
        return self._bound_port

    @property
    def open(self) -> bool:
        import time

        return (
            not self.state.paired
            and self._server is not None
            and time.monotonic() < self._deadline
        )

    def start(self) -> bool:
        """Open the window. Returns whether it opened.

        Refused, not silently skipped, when the robot is already paired: an
        enrollment listener on a paired robot would be a way to replace its
        certificate without anyone touching it.
        """
        import time

        if (self.certificate_directory / "server.crt").exists():
            self._report("pairing.skipped", {"reason": "already-paired"})
            return False
        self.state.load()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", self.port))
        except OSError as exc:
            server.close()
            self._report("pairing.unavailable", {"error": str(exc)})
            return False
        self._bound_port = server.getsockname()[1]
        server.listen(4)
        server.settimeout(1.0)
        self._server = server
        self._deadline = time.monotonic() + self.window_seconds
        self._thread = threading.Thread(target=self._serve, name="robot-enrollment", daemon=True)
        self._thread.start()
        self._report(
            "pairing.window-open",
            {"port": self._bound_port, "seconds": self.window_seconds, "attempts": self.max_attempts},
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass

    def _serve(self) -> None:
        import time

        while not self._stop.is_set():
            if time.monotonic() >= self._deadline:
                self._report("pairing.window-closed", {"reason": "expired"})
                self.stop()
                return
            try:
                connection, address = self._server.accept()  # type: ignore[union-attr]
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                self._handle(connection, address)
            except Exception as exc:  # noqa: BLE001 - one bad request must not end the server
                self._report("pairing.failed", {"error": str(exc)})
            finally:
                connection.close()

    def _handle(self, connection: socket.socket, address) -> None:
        connection.settimeout(10.0)
        reader = connection.makefile("rb")
        writer = connection.makefile("wb")
        try:
            request_bytes = read_frame(reader)
        except PairingError as exc:
            self._report("pairing.rejected", {"reason": str(exc), "peer": address[0]})
            return
        try:
            material, request = decode_request(self.state.code, request_bytes)
        except PairingError as exc:
            self.state.attempts += 1
            self._report(
                "pairing.rejected",
                {"reason": str(exc), "peer": address[0], "attempts": self.state.attempts},
            )
            if self.state.attempts >= self.max_attempts:
                # The window shuts rather than continuing to accept guesses. The
                # operator re-opens it locally, which is a physical act.
                self._report("pairing.window-closed", {"reason": "too-many-attempts"})
                self.stop()
            return
        if not codes_equal(request.get("robotId", ""), self.robot_id):
            # The material is bound to a robot id as additional authenticated data,
            # so this cannot normally happen; if it does, the code that opened the
            # payload belongs to a different robot's request.
            self._report("pairing.rejected", {"reason": "robot-id-mismatch", "peer": address[0]})
            return
        self._install(material)
        salt = base64.b64decode(request["salt"])
        nonce = secrets.token_bytes(12)
        answer = encode_result(
            self.state.code, salt, nonce, self.robot_id, {"status": "paired"}
        )
        write_frame(writer, answer)
        writer.flush()
        self._report("pairing.paired", {"peer": address[0], "robotId": self.robot_id})
        # Retired before the window closes: a code that outlives its pairing is a
        # permanent key to the robot.
        self.state.consume()
        self.stop()

    def _install(self, material: dict) -> None:
        """Write the material the way the SSH path writes it.

        Same three files, same modes, same directory: a robot paired this way must
        be byte-identical to one paired by ``scripts/pair-robot.sh``, or the two
        paths would drift into being two different kinds of pairing.
        """
        self.certificate_directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.certificate_directory, 0o700)
        except OSError:
            pass
        for name, key, mode in (
            ("server.key", "serverKey", 0o600),
            ("server.crt", "serverCert", 0o644),
            ("client-ca.crt", "ca", 0o644),
        ):
            path = self.certificate_directory / name
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(material[key], encoding="utf-8")
            os.chmod(temporary, mode)
            # Renamed into place so a reader never sees half a certificate.
            temporary.replace(path)

    def _report(self, event: str, detail: dict) -> None:
        if self.on_event is not None:
            self.on_event(event, detail)
