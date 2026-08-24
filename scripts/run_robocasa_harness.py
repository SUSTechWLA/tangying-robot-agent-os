"""Run RoboCasa acceptance and write a provenance-bound evidence pack."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

HANDOFF_PROMPT = "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区"
TASK_UPDATE_PROMPT = "最后放到右侧蓝色垫子上"

REQUIRED_ROBOT_IDS = ("robot-1", "robot-2")
EXPECTED_SOURCES = {
    "robot-1/proprioception",
    "robot-1/scene",
    "robot-2/proprioception",
    "robot-2/scene",
    "coordinator/resources/block:red-block",
}
VISUAL_SCREENSHOTS = ("overview", "robot-1", "robot-2", "handoff-final", "fallback")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
NONCE_RE = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_VIEWPORT = (1404, 794)
TRUSTED_ANCHOR_PATH = REPO / "tests/e2e/robocasa_golden_capture_anchor.json"
CANDIDATE_ROOT = REPO / "artifacts/robocasa-harness"
RETAINED_PACK_NAMES = frozenset({"round3", "round4"})
FRONTEND_RESOURCE_SPECS = (
    ("document", "index.html"),
    ("styles", "styles.css"),
    ("webgl", "webgl_scene.js"),
    ("world-view", "world_view.js"),
    ("app", "app.js"),
    ("manifest", "assets/scenes/robocasa-handoff-v1/manifest.json"),
    ("scene", "assets/scenes/robocasa-handoff-v1/scene.glb"),
    ("robot", "assets/scenes/robocasa-handoff-v1/xlerobot.glb"),
    ("binding", "assets/scenes/robocasa-handoff-v1/xlerobot.binding.json"),
)
NETWORK_ROLES = tuple(role for role, _path in FRONTEND_RESOURCE_SPECS)
RUNTIME_NO_QUERY_PATHS = frozenset(
    {
        "/healthz",
        "/favicon.ico",
        "/v1/auth/demo-session",
        "/v1/auth/ws-ticket",
        "/v1/devices",
        "/v1/maps/global",
        "/v1/scene/frames",
        "/v1/tasks",
        "/v1/world",
    }
)
RUNTIME_ROBOT_FRAME_RE = re.compile(r"/v1/scene/frames/robot-[12]\Z")


def start_robocasa_handoff_stack(*args, **kwargs):
    """Lazy, injectable boundary for candidate-mode simulator startup."""

    from tests.e2e.robocasa_harness import start_robocasa_handoff_stack as start

    return start(*args, **kwargs)


def _safe_relative_parts(relative: str | Path) -> tuple[str, ...]:
    path = Path(relative)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError("workspace path must be relative and cannot escape its root")
    return tuple(part for part in path.parts if part not in {"", "."})


class FDRootedDirectory:
    """Directory I/O rooted at a held fd; mutable pathnames are display-only."""

    def __init__(self, directory_fd: int, display_path: Path):
        self._directory_fd = directory_fd
        self.display_path = display_path

    @property
    def name(self) -> str:
        return self.display_path.name

    def __str__(self) -> str:
        return str(self.display_path)

    def __truediv__(self, relative: str | Path) -> FDRootedPath:
        return FDRootedPath(self, _safe_relative_parts(relative))

    def resolve(self) -> FDRootedDirectory:
        return self

    def is_dir(self) -> bool:
        try:
            return stat.S_ISDIR(os.fstat(self._directory_fd).st_mode)
        except OSError:
            return False

    def iterdir(self) -> list[FDRootedPath]:
        return [FDRootedPath(self, (name,)) for name in os.listdir(self._directory_fd)]

    def _open_directory(self, parts: tuple[str, ...], *, create: bool = False) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current_fd = os.dup(self._directory_fd)
        try:
            for component in parts:
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            return current_fd
        except BaseException:
            os.close(current_fd)
            raise

    def _parent_and_name(
        self, parts: tuple[str, ...], *, create_parent: bool = False
    ) -> tuple[int, str]:
        if not parts:
            raise ValueError("operation requires a path below the workspace root")
        return self._open_directory(parts[:-1], create=create_parent), parts[-1]

    def _stat(self, parts: tuple[str, ...]):
        if not parts:
            return os.fstat(self._directory_fd)
        parent_fd, name = self._parent_and_name(parts)
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        finally:
            os.close(parent_fd)

    def _write_bytes(self, parts: tuple[str, ...], payload: bytes) -> int:
        parent_fd, name = self._parent_and_name(parts)
        temporary = f".{name}.tmp-{secrets.token_hex(16)}"
        file_fd = None
        try:
            file_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            with os.fdopen(file_fd, "wb", closefd=True) as stream:
                file_fd = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            return len(payload)
        finally:
            if file_fd is not None:
                os.close(file_fd)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def _open_audited_file(self, parts: tuple[str, ...], metadata=None) -> int:
        if metadata is None:
            metadata = self._stat(parts)
        relative = str(Path(*parts))
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"unsafe evidence file type: {relative}")
        try:
            parent_fd, name = self._parent_and_name(parts)
        except OSError as error:
            raise ValueError(f"unsafe evidence file ancestor changed: {relative}") from error
        try:
            try:
                file_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise ValueError(f"unsafe evidence file changed: {relative}") from error
        finally:
            os.close(parent_fd)
        try:
            opened = os.fstat(file_fd)
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_dev,
                opened.st_ino,
            ) != (metadata.st_dev, metadata.st_ino):
                raise ValueError(f"unsafe evidence file identity changed: {relative}")
            return file_fd
        except BaseException:
            os.close(file_fd)
            raise

    def _read_bytes(self, parts: tuple[str, ...], metadata=None) -> bytes:
        file_fd = self._open_audited_file(parts, metadata)
        return _read_owned_descriptor(file_fd)

    def _clear_directory_fd(self, directory_fd: int) -> None:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        for name in os.listdir(directory_fd):
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(name, flags, dir_fd=directory_fd)
                try:
                    self._clear_directory_fd(child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(name, dir_fd=directory_fd)
            else:
                os.unlink(name, dir_fd=directory_fd)

    def clear(self) -> None:
        self._clear_directory_fd(self._directory_fd)

    def _walk(self, directory_fd: int, prefix: tuple[str, ...]) -> list[FDRootedPath]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        found: list[FDRootedPath] = []
        for name in sorted(os.listdir(directory_fd)):
            parts = (*prefix, name)
            path = FDRootedPath(self, parts)
            found.append(path)
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(name, flags, dir_fd=directory_fd)
                try:
                    found.extend(self._walk(child_fd, parts))
                finally:
                    os.close(child_fd)
        return found

    def rglob(self, pattern: str) -> list[FDRootedPath]:
        if pattern != "*":
            raise ValueError("fd-rooted workspace supports only rglob('*')")
        return self._walk(self._directory_fd, ())


class FDRootedPath:
    def __init__(self, root: FDRootedDirectory, parts: tuple[str, ...]):
        self.root = root
        self.parts = parts

    @property
    def display_path(self) -> Path:
        return self.root.display_path.joinpath(*self.parts)

    @property
    def name(self) -> str:
        return self.parts[-1] if self.parts else self.root.name

    @property
    def suffix(self) -> str:
        return self.display_path.suffix

    @property
    def parent(self) -> FDRootedPath:
        return FDRootedPath(self.root, self.parts[:-1])

    def __str__(self) -> str:
        return str(self.display_path)

    def __repr__(self) -> str:
        return f"FDRootedPath({self.display_path!s})"

    def __hash__(self) -> int:
        return hash((id(self.root), self.parts))

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, FDRootedPath)
            and self.root is other.root
            and self.parts == other.parts
        )

    def __lt__(self, other) -> bool:
        return str(self) < str(other)

    def __truediv__(self, relative: str | Path) -> FDRootedPath:
        return FDRootedPath(self.root, (*self.parts, *_safe_relative_parts(relative)))

    def resolve(self) -> FDRootedPath:
        return self

    def relative_to(self, other) -> Path:
        if other is self.root:
            return Path(*self.parts)
        if isinstance(other, FDRootedPath) and other.root is self.root:
            prefix = other.parts
            if self.parts[: len(prefix)] != prefix:
                raise ValueError("path is outside requested fd-rooted prefix")
            return Path(*self.parts[len(prefix) :])
        raise ValueError("path is outside fd-rooted workspace")

    def exists(self) -> bool:
        try:
            self.root._stat(self.parts)
            return True
        except (FileNotFoundError, NotADirectoryError, OSError):
            return False

    def is_file(self) -> bool:
        try:
            return stat.S_ISREG(self.root._stat(self.parts).st_mode)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return False

    def is_dir(self) -> bool:
        try:
            return stat.S_ISDIR(self.root._stat(self.parts).st_mode)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return False

    def lstat(self):
        return self.root._stat(self.parts)

    def read_bytes(self, metadata=None) -> bytes:
        return self.root._read_bytes(self.parts, metadata)

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)

    def write_bytes(self, payload: bytes) -> int:
        return self.root._write_bytes(self.parts, payload)

    def write_text(self, text: str, encoding: str = "utf-8") -> int:
        self.write_bytes(text.encode(encoding))
        return len(text)

    def open(self, mode: str = "r", encoding: str | None = None):
        if mode not in {"r", "rb"}:
            raise ValueError("fd-rooted path open supports read-only modes")
        payload = self.read_bytes()
        if mode == "rb":
            return io.BytesIO(payload)
        return io.StringIO(payload.decode(encoding or "utf-8"))

    def chmod(self, mode: int) -> None:
        file_fd = self.root._open_audited_file(self.parts)
        try:
            os.fchmod(file_fd, mode)
        finally:
            os.close(file_fd)

    def unlink(self, missing_ok: bool = False) -> None:
        parent_fd, name = self.root._parent_and_name(self.parts)
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            if not missing_ok:
                raise
        finally:
            os.close(parent_fd)

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        if not self.parts:
            if exist_ok:
                return
            raise FileExistsError(str(self))
        if parents:
            directory_fd = self.root._open_directory(self.parts, create=True)
            os.close(directory_fd)
            return
        parent_fd, name = self.root._parent_and_name(self.parts)
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            if not exist_ok:
                raise
        finally:
            os.close(parent_fd)

    def iterdir(self) -> list[FDRootedPath]:
        directory_fd = self.root._open_directory(self.parts)
        try:
            return [
                FDRootedPath(self.root, (*self.parts, name)) for name in os.listdir(directory_fd)
            ]
        finally:
            os.close(directory_fd)


def write_json(path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def canonical_digest(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _expected_frontend_build() -> dict:
    resources = []
    web_root = REPO / "web"
    for role, source_path in FRONTEND_RESOURCE_SPECS:
        payload = (web_root / source_path).read_bytes()
        served_path = "/" if role == "document" else f"/{source_path}"
        resources.append(
            {
                "role": role,
                "sourcePath": f"web/{source_path}",
                "servedPath": served_path,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    unsigned = {
        "schemaVersion": "tangying.frontend-build.v1",
        "resources": resources,
    }
    return {**unsigned, "digest": canonical_digest(unsigned)}


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _read_owned_descriptor(file_fd: int, digest=None) -> bytes:
    """Consume one owned descriptor without transferring its ownership."""
    chunks = [] if digest is None else None
    active_error = None
    try:
        while chunk := os.read(file_fd, 1024 * 1024):
            if digest is None:
                chunks.append(chunk)
            else:
                digest.update(chunk)
    except BaseException as error:
        active_error = error
        raise
    finally:
        try:
            os.close(file_fd)
        except BaseException as close_error:
            if active_error is None:
                raise
            active_error.add_note(f"owned evidence fd close also failed: {close_error!r}")
    return b"".join(chunks) if chunks is not None else b""


def _open_absolute_directory_no_symlinks(path: Path) -> int:
    """Open every component of a fixed absolute directory path without links."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current_fd = os.open(absolute.anchor, flags)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _directory_identity_matches(path: Path, directory_fd: int) -> bool:
    verification_fd = None
    try:
        verification_fd = _open_absolute_directory_no_symlinks(path)
        current = os.fstat(verification_fd)
        held = os.fstat(directory_fd)
    except OSError:
        return False
    finally:
        if verification_fd is not None:
            os.close(verification_fd)
    return (current.st_dev, current.st_ino) == (held.st_dev, held.st_ino)


@contextmanager
def _held_evidence_root(output):
    """Hold one canonical pack root across enumeration and every direct read."""
    if isinstance(output, FDRootedDirectory):
        yield output
        return
    canonical = Path(output).resolve(strict=True)
    directory_fd = _open_absolute_directory_no_symlinks(canonical)
    rooted = FDRootedDirectory(directory_fd, canonical)
    try:
        yield rooted
        if not _directory_identity_matches(canonical, directory_fd):
            raise ValueError("unsafe evidence root changed during operation")
    finally:
        os.close(directory_fd)


def _path_from_held_root(root: FDRootedDirectory, path):
    if isinstance(path, FDRootedPath):
        return path
    try:
        canonical = Path(path).resolve(strict=True)
        relative = canonical.relative_to(root.display_path)
    except (OSError, ValueError):
        return path
    return root / relative


def _open_audited_regular_file(path, metadata=None, *, label: str | None = None) -> int:
    """Open exactly the audited regular inode without following or blocking."""
    relative = label or str(path)
    if isinstance(path, FDRootedPath):
        if metadata is None:
            try:
                metadata = path.lstat()
            except OSError as error:
                raise ValueError(f"unsafe evidence file cannot be inspected: {relative}") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"unsafe evidence file type: {relative}")
        return path.root._open_audited_file(path.parts, metadata)
    parent_fd = None
    file_fd = None
    try:
        canonical_parent = path.parent.resolve(strict=True)
        parent_fd = _open_absolute_directory_no_symlinks(canonical_parent)
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"unsafe evidence file type: {relative}")
        file_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"unsafe evidence file identity changed: {relative}")
        if not _directory_identity_matches(canonical_parent, parent_fd):
            raise ValueError(f"unsafe evidence file ancestor changed: {relative}")
        return file_fd
    except OSError as error:
        if file_fd is not None:
            os.close(file_fd)
        raise ValueError(f"unsafe evidence file changed: {relative}") from error
    except BaseException:
        if file_fd is not None:
            os.close(file_fd)
        raise
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _read_audited_regular_file(path, metadata=None, *, label: str | None = None) -> bytes:
    file_fd = _open_audited_regular_file(path, metadata, label=label)
    return _read_owned_descriptor(file_fd)


def _sha256_audited_regular_file(path, metadata=None, *, label: str | None = None) -> str:
    file_fd = _open_audited_regular_file(path, metadata, label=label)
    digest = hashlib.sha256()
    _read_owned_descriptor(file_fd, digest)
    return digest.hexdigest()


def _safe_evidence_files(output, excluded: set[str]) -> dict[str, str]:
    if not isinstance(output, FDRootedDirectory):
        display_path = Path(output)
        try:
            canonical_path = display_path.resolve(strict=True)
            directory_fd = _open_absolute_directory_no_symlinks(canonical_path)
        except OSError as error:
            raise ValueError("unsafe evidence root cannot be opened") from error
        rooted = FDRootedDirectory(directory_fd, display_path)
        try:
            files = _safe_evidence_files(rooted, excluded)
            if not _directory_identity_matches(canonical_path, directory_fd):
                raise ValueError("unsafe evidence root changed during enumeration")
            return files
        except OSError as error:
            raise ValueError("unsafe evidence tree changed during enumeration") from error
        finally:
            os.close(directory_fd)
    files: dict[str, str] = {}
    for path in sorted(output.rglob("*")):
        relative = str(path.relative_to(output))
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ValueError(
                f"unsafe evidence tree entry cannot be inspected: {relative}"
            ) from error
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"unsafe evidence tree entry type: {relative}")
        if relative not in excluded:
            files[relative] = _sha256_audited_regular_file(path, metadata, label=relative)
    return files


def _capture_files(output: Path) -> dict[str, str]:
    excluded = {
        "acceptance-attestation.json",
        "capture-envelope.json",
        "capture-session.json",
        "capture-anchor-candidate.json",
        "summary.json",
    }
    return _safe_evidence_files(output, excluded)


def _openssl(*arguments: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["openssl", *arguments], input=input_bytes, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode(errors="replace"))
    return completed.stdout


def seal_capture_pack(
    output: Path,
    *,
    run_id: str,
    episode_nonce: str,
    task_id: str,
    trusted_anchor_path: Path,
) -> dict:
    """Seal runner-written artifacts; the ephemeral private key never leaves its temp dir."""
    files = _capture_files(output)
    with tempfile.TemporaryDirectory(prefix="tangying-capture-key-") as directory:
        private_key = Path(directory) / "private.pem"
        public_key = Path(directory) / "public.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private_key)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
            check=True,
            capture_output=True,
        )
        public_pem = public_key.read_text()
        public_der = _openssl("pkey", "-pubin", "-in", str(public_key), "-outform", "DER")
        unsigned = {
            "schemaVersion": "tangying.authenticated-capture.v1",
            "runId": run_id,
            "episodeNonce": episode_nonce,
            "taskId": task_id,
            "sealedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "publicKeyPem": public_pem,
            "publicKeyFingerprint": hashlib.sha256(public_der).hexdigest(),
            "files": files,
            "manifestDigest": canonical_digest(files),
        }
        message = Path(directory) / "message.json"
        signature = Path(directory) / "signature.bin"
        message.write_bytes(_canonical_bytes(unsigned))
        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(private_key),
                "-in",
                str(message),
                "-out",
                str(signature),
            ],
            check=True,
            capture_output=True,
        )
        envelope = {**unsigned, "signature": base64.b64encode(signature.read_bytes()).decode()}
    envelope_path = output / "capture-envelope.json"
    write_json(envelope_path, envelope)
    anchor = {
        "schemaVersion": "tangying.trusted-capture-anchor.v1",
        "runId": run_id,
        "episodeNonce": episode_nonce,
        "taskId": task_id,
        "publicKeyPem": envelope["publicKeyPem"],
        "publicKeyFingerprint": envelope["publicKeyFingerprint"],
        "captureEnvelopeSha256": _sha256_audited_regular_file(envelope_path),
    }
    trusted_anchor_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(trusted_anchor_path, anchor)
    return anchor


def _attestation_files(output: Path) -> dict[str, str]:
    excluded = {
        "acceptance-attestation.json",
        "capture-session.json",
        "capture-anchor-candidate.json",
    }
    return _safe_evidence_files(output, excluded)


def _generate_ed25519_key(directory: Path) -> tuple[Path, Path, str, str]:
    private_key = directory / "private.pem"
    public_key = directory / "public.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private_key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        check=True,
        capture_output=True,
    )
    public_pem = public_key.read_text()
    public_der = _openssl("pkey", "-pubin", "-in", str(public_key), "-outform", "DER")
    return private_key, public_key, public_pem, hashlib.sha256(public_der).hexdigest()


def _sign_document(unsigned: dict, private_key: Path, directory: Path) -> str:
    message = directory / f"message-{uuid.uuid4().hex}.json"
    signature = directory / f"signature-{uuid.uuid4().hex}.bin"
    message.write_bytes(_canonical_bytes(unsigned))
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            str(private_key),
            "-in",
            str(message),
            "-out",
            str(signature),
        ],
        check=True,
        capture_output=True,
    )
    return base64.b64encode(signature.read_bytes()).decode()


def _verify_signed_document(document: dict) -> bool:
    unsigned = dict(document)
    signature_text = unsigned.pop("signature", None)
    try:
        signature = base64.b64decode(signature_text, validate=True)
        public_pem = unsigned["publicKeyPem"]
    except (KeyError, TypeError, ValueError):
        return False
    with tempfile.TemporaryDirectory(prefix="tangying-attestation-verify-") as directory:
        root = Path(directory)
        public_key = root / "public.pem"
        message = root / "message.json"
        signature_path = root / "signature.bin"
        try:
            public_key.write_text(public_pem)
            message.write_bytes(_canonical_bytes(unsigned))
            signature_path.write_bytes(signature)
            result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-rawin",
                    "-pubin",
                    "-inkey",
                    str(public_key),
                    "-in",
                    str(message),
                    "-sigfile",
                    str(signature_path),
                ],
                capture_output=True,
                check=False,
            )
        except (OSError, TypeError):
            return False
    return result.returncode == 0


def _public_key_fingerprint(public_pem: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="tangying-public-key-") as directory:
        public_key = Path(directory) / "public.pem"
        try:
            public_key.write_text(public_pem)
            public_der = _openssl("pkey", "-pubin", "-in", str(public_key), "-outform", "DER")
        except (OSError, RuntimeError, TypeError):
            return None
    return hashlib.sha256(public_der).hexdigest()


def _write_capture_envelope(
    output: Path,
    *,
    run_id: str,
    episode_nonce: str,
    task_id: str,
    private_key: Path,
    public_pem: str,
    public_fingerprint: str,
    key_directory: Path,
) -> dict:
    files = _capture_files(output)
    unsigned = {
        "schemaVersion": "tangying.authenticated-capture.v2",
        "runId": run_id,
        "episodeNonce": episode_nonce,
        "taskId": task_id,
        "sealedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "publicKeyPem": public_pem,
        "publicKeyFingerprint": public_fingerprint,
        "files": files,
        "manifestDigest": canonical_digest(files),
    }
    envelope = {
        **unsigned,
        "signature": _sign_document(unsigned, private_key, key_directory),
    }
    write_json(output / "capture-envelope.json", envelope)
    return envelope


def _write_final_attestation(
    output: Path,
    *,
    run_id: str,
    episode_nonce: str,
    task_id: str,
    private_key: Path,
    public_pem: str,
    public_fingerprint: str,
    key_directory: Path,
    candidate_anchor_path: Path,
) -> dict:
    if (output / "capture-session.json").exists():
        raise AssertionError("capture session must be retired before final attestation")
    summary_path = output / "summary.json"
    envelope_path = output / "capture-envelope.json"
    if not summary_path.is_file() or not envelope_path.is_file():
        raise AssertionError("summary and capture envelope are required before finalization")
    files = _attestation_files(output)
    summary_hash = _sha256_audited_regular_file(summary_path)
    envelope_hash = _sha256_audited_regular_file(envelope_path)
    unsigned = {
        "schemaVersion": "tangying.robocasa-acceptance-attestation.v1",
        "runId": run_id,
        "episodeNonce": episode_nonce,
        "taskId": task_id,
        "finalizedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "publicKeyPem": public_pem,
        "publicKeyFingerprint": public_fingerprint,
        "summarySha256": summary_hash,
        "captureEnvelopeSha256": envelope_hash,
        "files": files,
        "manifestDigest": canonical_digest(files),
    }
    attestation = {
        **unsigned,
        "signature": _sign_document(unsigned, private_key, key_directory),
    }
    attestation_path = output / "acceptance-attestation.json"
    write_json(attestation_path, attestation)
    anchor = {
        "schemaVersion": "tangying.trusted-acceptance-anchor.v2",
        "runId": run_id,
        "episodeNonce": episode_nonce,
        "taskId": task_id,
        "publicKeyPem": public_pem,
        "publicKeyFingerprint": public_fingerprint,
        "summarySha256": summary_hash,
        "captureEnvelopeSha256": envelope_hash,
        "attestationSha256": _sha256_audited_regular_file(attestation_path),
    }
    candidate_anchor_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(candidate_anchor_path, anchor)
    return anchor


def finalize_acceptance_pack(
    output: Path,
    *,
    run_id: str,
    episode_nonce: str,
    task_id: str,
    candidate_anchor_path: Path,
) -> dict:
    """Create a final attestation with a key that is destroyed before returning."""
    output = output.resolve()
    if (output / "capture-session.json").exists():
        raise AssertionError("capture session must be retired before final attestation")
    _safe_evidence_files(output, set())
    stale_attestation = output / "acceptance-attestation.json"
    if stale_attestation.exists():
        stale_attestation.unlink()
    with tempfile.TemporaryDirectory(prefix="tangying-acceptance-key-") as directory:
        root = Path(directory)
        private_key, _public_key, public_pem, fingerprint = _generate_ed25519_key(root)
        _write_capture_envelope(
            output,
            run_id=run_id,
            episode_nonce=episode_nonce,
            task_id=task_id,
            private_key=private_key,
            public_pem=public_pem,
            public_fingerprint=fingerprint,
            key_directory=root,
        )
        return _write_final_attestation(
            output,
            run_id=run_id,
            episode_nonce=episode_nonce,
            task_id=task_id,
            private_key=private_key,
            public_pem=public_pem,
            public_fingerprint=fingerprint,
            key_directory=root,
            candidate_anchor_path=candidate_anchor_path,
        )


def _authenticated_capture_valid(
    output: Path,
    run_context: dict,
    task_id: str,
    trusted_anchor_path: Path | None = None,
) -> bool:
    try:
        actual_files = _capture_files(output)
    except (OSError, ValueError):
        return False
    envelope_path = output / "capture-envelope.json"
    envelope = _load_json(envelope_path)
    if envelope is None:
        return False
    identity_valid = (
        envelope.get("schemaVersion")
        in {"tangying.authenticated-capture.v1", "tangying.authenticated-capture.v2"}
        and envelope.get("runId") == run_context.get("runId")
        and envelope.get("episodeNonce") == run_context.get("episodeNonce")
        and envelope.get("taskId") == task_id
        and _public_key_fingerprint(envelope.get("publicKeyPem"))
        == envelope.get("publicKeyFingerprint")
    )
    files = envelope.get("files")
    if not identity_valid or not isinstance(files, dict) or files != actual_files:
        return False
    if envelope.get("manifestDigest") != canonical_digest(files):
        return False
    return _verify_signed_document(envelope)


class AuthenticatedCaptureReceiver:
    """Loopback-only, bearer-authenticated ingress owned by the acceptance runner."""

    def __init__(
        self,
        output: Path,
        *,
        run_id: str,
        episode_nonce: str,
        integrity_check=None,
    ):
        self.output = output
        self.run_id = run_id
        self.episode_nonce = episode_nonce
        self._integrity_check = integrity_check or (lambda: None)
        self.bearer_secret = secrets.token_urlsafe(48)
        self.task_id: str | None = None
        self.trusted_anchor_path: Path | None = None
        self._received = threading.Event()
        self._serve_started = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._reservation_lock = threading.Lock()
        self._reservation_state = "open"
        self._reservation_files: dict[Path, str] = {}
        self._stopped = False
        self._key_temp: tempfile.TemporaryDirectory | None = None
        self._key_root: Path | None = None
        self._private_key: Path | None = None
        self._public_pem: str | None = None
        self._public_fingerprint: str | None = None

    @property
    def private_key_active(self) -> bool:
        return self._private_key is not None and self._private_key.is_file()

    def _destroy_private_key(self) -> None:
        if self._key_temp is not None:
            self._key_temp.cleanup()
        self._key_temp = None
        self._key_root = None
        self._private_key = None
        self._public_pem = None
        self._public_fingerprint = None

    def _receiver_files(self) -> dict[Path, str]:
        self._integrity_check()
        paths = {
            self.output / "browser-evidence.json",
            self.output / "visual-network.json",
            self.output / "visual-performance.json",
            self.output / "capture-envelope.json",
        }
        visual = self.output / "visual"
        if visual.exists():
            paths.update(visual.iterdir())
        files: dict[Path, str] = {}
        for path in paths:
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            files[path] = _sha256_audited_regular_file(path, metadata)
        return files

    def _reserve(self) -> bool:
        with self._reservation_lock:
            if self._reservation_state != "open":
                return False
            self._reservation_state = "reserved"
            self._reservation_files = self._receiver_files()
            return True

    def _release_if_uncommitted(self) -> None:
        with self._reservation_lock:
            if self._reservation_state != "reserved":
                return
            if self._receiver_files() == self._reservation_files:
                self._reservation_state = "open"
                self._reservation_files = {}
            else:
                self._reservation_state = "committed"

    def _commit_reservation(self) -> None:
        with self._reservation_lock:
            self._reservation_state = "committed"

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("capture receiver is not running")
        return f"http://127.0.0.1:{self._server.server_port}/v1/capture"

    def _write_session(self) -> None:
        self._integrity_check()
        session = {
            "schemaVersion": "tangying.capture-session.v1",
            "runId": self.run_id,
            "episodeNonce": self.episode_nonce,
            "taskId": self.task_id,
            "receiverUrl": self.url,
            "bearerSecret": self.bearer_secret,
        }
        path = self.output / "capture-session.json"
        write_json(path, session)
        path.chmod(0o600)
        self._integrity_check()

    def start(self) -> None:
        self._key_temp = tempfile.TemporaryDirectory(prefix="tangying-capture-key-")
        self._key_root = Path(self._key_temp.name)
        (
            self._private_key,
            _public_key,
            self._public_pem,
            self._public_fingerprint,
        ) = _generate_ed25519_key(self._key_root)
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != "/v1/capture":
                    self.send_error(404)
                    return
                expected = f"Bearer {receiver.bearer_secret}"
                if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                    self.send_error(401)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if size <= 0 or size > 80 * 1024 * 1024:
                    self.send_error(413)
                    return
                if not receiver._reserve():
                    self.send_error(409)
                    return
                try:
                    payload = json.loads(self.rfile.read(size))
                    receiver._accept(payload)
                except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
                    receiver._release_if_uncommitted()
                    self.send_error(422, str(error))
                    return
                receiver._commit_reservation()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"accepted":true}')

            def log_message(self, _format, *_arguments) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server = self._server

        def serve() -> None:
            self._serve_started.set()
            server.serve_forever()

        self._thread = threading.Thread(target=serve, name="robocasa-capture-receiver", daemon=True)
        self._thread.start()
        self._write_session()

    def bind_task(self, task_id: str, trusted_anchor_path: Path) -> None:
        self.task_id = task_id
        self.trusted_anchor_path = trusted_anchor_path
        self._write_session()

    def _accept(self, payload: dict) -> None:
        self._integrity_check()
        if not (
            isinstance(payload, dict)
            and payload.get("schemaVersion") == "tangying.browser-capture-upload.v1"
            and payload.get("runId") == self.run_id
            and payload.get("episodeNonce") == self.episode_nonce
            and payload.get("taskId") == self.task_id
            and self.task_id is not None
            and self.trusted_anchor_path is not None
        ):
            raise AssertionError("capture episode identity mismatch")
        browser = payload.get("browserEvidence")
        network = payload.get("network")
        performance = payload.get("performance")
        screenshots = payload.get("screenshots")
        world_snapshots = payload.get("worldSnapshots")
        if (
            not isinstance(browser, dict)
            or not isinstance(network, dict)
            or not isinstance(performance, dict)
            or not isinstance(screenshots, dict)
            or not isinstance(world_snapshots, dict)
            or set(world_snapshots) != set(screenshots)
            or network.get("schemaVersion") != "tangying.browser-network.v2"
            or network.get("runId") != self.run_id
            or network.get("episodeNonce") != self.episode_nonce
            or network.get("taskId") != self.task_id
            or browser.get("networkDigest") != canonical_digest(network)
        ):
            raise AssertionError("capture upload is incomplete")
        visual_dir = self.output / "visual"
        visual_dir.mkdir(exist_ok=True)
        import io

        from PIL import Image

        screenshot_records = browser.setdefault("screenshots", {})
        snapshot_records = browser.setdefault("snapshots", {})
        for name, encoded in screenshots.items():
            if name not in VISUAL_SCREENSHOTS or not isinstance(encoded, str):
                raise AssertionError("unexpected screenshot")
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as opened:
                opened.load()
                image = opened.convert("RGB")
                if image.size != EXPECTED_VIEWPORT:
                    raise AssertionError("screenshot viewport mismatch")
                path = visual_dir / f"{name}.png"
                encoded_png = io.BytesIO()
                image.save(encoded_png, "PNG")
                path.write_bytes(encoded_png.getvalue())
            saved = encoded_png.getvalue()
            snapshot = world_snapshots[name]
            if not (
                isinstance(snapshot, dict)
                and snapshot.get("schemaVersion") == "world.snapshot.v1"
                and snapshot.get("acceptanceNonce") == self.episode_nonce
                and type(snapshot.get("revision")) is int
            ):
                raise AssertionError("screenshot world snapshot mismatch")
            snapshot_path = visual_dir / f"world-{name}.json"
            write_json(snapshot_path, snapshot)
            snapshot_digest = canonical_digest(snapshot)
            snapshot_records[name] = {
                "path": f"visual/world-{name}.json",
                "revision": snapshot["revision"],
                "projectedAt": snapshot.get("projectedAt"),
                "digest": snapshot_digest,
            }
            record = screenshot_records.setdefault(name, {})
            record.update(
                {
                    "path": f"visual/{name}.png",
                    "format": "png",
                    "sha256": hashlib.sha256(saved).hexdigest(),
                    "bytes": len(saved),
                    "capturedAt": browser.get("captures", {}).get(name, {}).get("capturedAt"),
                    "receivedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "worldRevision": snapshot["revision"],
                    "worldDigest": snapshot_digest,
                    "episodeNonce": self.episode_nonce,
                    "taskId": self.task_id,
                }
            )
        browser["receiverAuthenticated"] = True
        browser["receivedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        write_json(self.output / "browser-evidence.json", browser)
        write_json(self.output / "visual-network.json", network)
        write_json(self.output / "visual-performance.json", performance)
        if not all(
            (
                self._private_key,
                self._public_pem,
                self._public_fingerprint,
                self._key_root,
            )
        ):
            raise AssertionError("capture signing key is unavailable")
        _write_capture_envelope(
            self.output,
            run_id=self.run_id,
            episode_nonce=self.episode_nonce,
            task_id=self.task_id,
            private_key=self._private_key,
            public_pem=self._public_pem,
            public_fingerprint=self._public_fingerprint,
            key_directory=self._key_root,
        )
        self._integrity_check()
        self._received.set()

    def finalize(self, summary: dict, candidate_anchor_path: Path) -> dict:
        self._integrity_check()
        if not self._received.is_set():
            raise AssertionError("browser capture was not received")
        if not all(
            (
                self._private_key,
                self._public_pem,
                self._public_fingerprint,
                self._key_root,
            )
        ):
            raise AssertionError("capture signing key is unavailable")
        (self.output / "capture-session.json").unlink(missing_ok=True)
        write_json(self.output / "summary.json", summary)
        try:
            anchor = _write_final_attestation(
                self.output,
                run_id=self.run_id,
                episode_nonce=self.episode_nonce,
                task_id=str(self.task_id),
                private_key=self._private_key,
                public_pem=self._public_pem,
                public_fingerprint=self._public_fingerprint,
                key_directory=self._key_root,
                candidate_anchor_path=candidate_anchor_path,
            )
            with self._reservation_lock:
                self._reservation_state = "finalized"
            self._integrity_check()
            return anchor
        finally:
            self._destroy_private_key()

    def wait(self, timeout: float) -> bool:
        return self._received.wait(timeout)

    def stop(self) -> None:
        if self._stopped:
            return
        server = self._server
        thread = self._thread
        try:
            if server is not None:
                try:
                    if thread is not None and thread.is_alive() and self._serve_started.is_set():
                        server.shutdown()
                finally:
                    server.server_close()
        finally:
            try:
                if thread is not None and thread.is_alive():
                    thread.join(timeout=5)
            finally:
                try:
                    (self.output / "capture-session.json").unlink(missing_ok=True)
                finally:
                    try:
                        self._destroy_private_key()
                    finally:
                        self._stopped = True


def _real_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _load_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        value = json.loads(_read_audited_regular_file(path).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_json_list(path: Path | None) -> list | None:
    if path is None:
        return None
    try:
        value = json.loads(_read_audited_regular_file(path).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) else None


def _origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc


def _model_identity(world: dict) -> tuple[str, str, str | None]:
    identities = {
        (
            attributes.get("scene_id"),
            attributes.get("model_hash"),
            attributes.get("adapter"),
        )
        for entity in world.get("entities", {}).values()
        if (attributes := entity.get("attributes", {})).get("scene_id")
        or attributes.get("model_hash")
    }
    if len(identities) != 1:
        raise AssertionError(f"expected one authoritative model identity: {identities}")
    scene_id, model_hash, adapter = identities.pop()
    if not isinstance(scene_id, str) or not scene_id:
        raise AssertionError(f"invalid scene identity: {scene_id!r}")
    if not isinstance(model_hash, str) or SHA256_RE.fullmatch(model_hash) is None:
        raise AssertionError(f"invalid model identity: {model_hash!r}")
    return scene_id, model_hash, adapter


def _canonical_joints(world: dict) -> dict[str, dict[str, float]]:
    return {
        robot_id: {
            key: value
            for key, value in world.get("robots", {}).get(robot_id, {}).get("state", {}).items()
            if key.startswith("joint.") and _real_number(value)
        }
        for robot_id in REQUIRED_ROBOT_IDS
    }


def _canonical_joints_valid(world: dict) -> bool:
    robots = world.get("robots", {})
    if set(robots) != set(REQUIRED_ROBOT_IDS):
        return False
    for robot_id in REQUIRED_ROBOT_IDS:
        state = robots[robot_id].get("state", {})
        declared = {key: value for key, value in state.items() if key.startswith("joint.")}
        if len(declared) < 12 or not all(_real_number(value) for value in declared.values()):
            return False
    return True


def _joint_movement_valid(initial: dict, moving: dict) -> bool:
    if not (_canonical_joints_valid(initial) and _canonical_joints_valid(moving)):
        return False
    initial_joints = _canonical_joints(initial)
    moving_joints = _canonical_joints(moving)
    return all(
        set(initial_joints[robot_id]) == set(moving_joints[robot_id])
        and any(
            abs(value - initial_joints[robot_id][name]) > 1e-3
            for name, value in moving_joints[robot_id].items()
        )
        for robot_id in REQUIRED_ROBOT_IDS
    )


def _task_identity_valid(run_context: dict, task_id: str, task: dict) -> bool:
    return (
        isinstance(task_id, str)
        and bool(task_id)
        and run_context.get("taskId") == task_id
        and task.get("id") == task_id
        and run_context.get("request") == HANDOFF_PROMPT
        and task.get("request") == TASK_UPDATE_PROMPT
        and task.get("currentRevision") == 2
        and run_context.get("adapter") == "robocasa"
        and task.get("adapter") == "robocasa"
        and task.get("state") == "SUCCEEDED"
    )


def _intents_valid(intents: list[dict]) -> bool:
    if not isinstance(intents, list) or len(intents) != 2:
        return False
    for expected_index, expected_robot in enumerate(REQUIRED_ROBOT_IDS):
        intent = intents[expected_index]
        evidence = intent.get("harnessEvidenceIds")
        if not (
            intent.get("index") == expected_index
            and intent.get("robotId") == expected_robot
            and intent.get("status") == "SUCCEEDED"
            and intent.get("harnessStatus") == "SATISFIED"
            and intent.get("harnessReason") == "PHYSICAL_POSTCONDITIONS_SATISFIED"
            and isinstance(evidence, list)
            and len(evidence) >= 2
            and all(isinstance(item, str) and item for item in evidence)
            and type(intent.get("fencingToken")) is int
            and intent["fencingToken"] > 0
        ):
            return False
    tokens = [intent["fencingToken"] for intent in intents]
    return len(set(tokens)) == 2 and tokens == sorted(tokens)


def _scene_identity_valid(run_context: dict, world: dict, manifest: dict) -> bool:
    try:
        scene_id, model_hash, adapter = _model_identity(world)
    except AssertionError:
        return False
    return (
        world.get("schemaVersion") == "world.snapshot.v1"
        and world.get("worldId") == "robocasa-handoff-v1"
        and run_context.get("sceneId") == "robocasa-handoff-v1"
        and scene_id == "robocasa-handoff-v1"
        and adapter == "robocasa"
        and manifest.get("sceneId") == scene_id
        and manifest.get("modelHash") == model_hash
        and SHA256_RE.fullmatch(model_hash) is not None
    )


def _final_held_clear(world: dict) -> bool:
    robots = world.get("robots", {})
    red_relations = world.get("entities", {}).get("red-block", {}).get("relations", {})
    return all(
        not robots.get(robot_id, {}).get("held") for robot_id in REQUIRED_ROBOT_IDS
    ) and not red_relations.get("held_by")


def _source_freshness_valid(world: dict) -> bool:
    sources = world.get("sources", {})
    return EXPECTED_SOURCES.issubset(sources) and all(
        source.get("freshness") == "FRESH" for source in sources.values()
    )


def _custody_valid(world: dict, intents: list[dict]) -> bool:
    resource = world.get("resources", {}).get("block:red-block", {})
    tokens = [item.get("fencingToken") for item in intents]
    return (
        _intents_valid(intents)
        and resource.get("owner") == "environment"
        and resource.get("freshness") == "FRESH"
        and type(resource.get("fencingToken")) is int
        and resource["fencingToken"] > max(tokens)
    )


def _custody_trajectory_valid(
    trajectory: dict | None, run_context: dict, task_id: str, intents: list[dict], final: dict
) -> bool:
    if not (
        isinstance(trajectory, dict)
        and trajectory.get("schemaVersion") == "tangying.world-trajectory.v1"
        and trajectory.get("episodeNonce") == run_context.get("episodeNonce")
        and trajectory.get("taskId") == task_id
        and isinstance(trajectory.get("samples"), list)
        and len(trajectory["samples"]) >= 4
        and _intents_valid(intents)
    ):
        return False
    samples = trajectory["samples"]
    revisions = [sample.get("revision") for sample in samples if isinstance(sample, dict)]
    projected = [_parse_timestamp(sample.get("projectedAt")) for sample in samples]
    if not (
        len(revisions) == len(samples)
        and all(type(value) is int for value in revisions)
        and all(left < right for left, right in pairwise(revisions))
        and all(value is not None for value in projected)
        and all(left < right for left, right in pairwise(projected))
        and all(
            sample.get("acceptanceNonce") == run_context.get("episodeNonce") for sample in samples
        )
    ):
        return False
    observed_tokens = [
        resource.get("fencingToken")
        for sample in samples
        if isinstance((resource := sample.get("resources", {}).get("block:red-block")), dict)
    ]
    if not (
        observed_tokens
        and all(type(token) is int for token in observed_tokens)
        and all(left <= right for left, right in pairwise(observed_tokens))
    ):
        return False
    for intent in intents:
        robot_id = intent["robotId"]
        token = intent["fencingToken"]
        if not any(
            sample.get("resources", {}).get("block:red-block", {}).get("owner") == robot_id
            and sample.get("resources", {}).get("block:red-block", {}).get("fencingToken") == token
            and sample.get("resources", {}).get("block:red-block", {}).get("freshness") == "FRESH"
            and sample.get("robots", {}).get(robot_id, {}).get("held") == "red-block"
            for sample in samples
        ):
            return False
    final_resource = final.get("resources", {}).get("block:red-block", {})
    return (
        final_resource.get("owner") == "environment"
        and [intent.get("fencingToken") for intent in intents] == [1, 2]
        and final_resource.get("freshness") == "FRESH"
        and final_resource.get("fencingToken") == intents[-1]["fencingToken"] + 1
        and samples[-1].get("revision") == final.get("revision")
        and canonical_digest(samples[-1]) == canonical_digest(final)
        and _final_held_clear(samples[-1])
    )


def _harness_evidence_valid(
    events: list | None,
    task_id: str,
    intents: list[dict],
    final: dict,
    trajectory: dict | None,
) -> bool:
    trajectory_samples = trajectory.get("samples") if isinstance(trajectory, dict) else None
    if (
        not isinstance(events, list)
        or len(intents) != 2
        or not isinstance(trajectory_samples, list)
    ):
        return False
    physical_events = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("eventType") in {"BLOCK_AVAILABLE", "BLOCK_DELIVERED"}
    ]
    if len(physical_events) != 2:
        return False
    expected_types = ("BLOCK_AVAILABLE", "BLOCK_DELIVERED")
    final_token = final.get("resources", {}).get("block:red-block", {}).get("fencingToken")
    for index, (intent, event, event_type) in enumerate(
        zip(intents, physical_events, expected_types, strict=True)
    ):
        payload = event.get("payload", {})
        verdict = payload.get("harness", {})
        transition = payload.get("resourceTransition", {})
        evidence_ids = intent.get("harnessEvidenceIds")
        observations = verdict.get("observations")
        if not (
            event.get("aggregateId") == task_id
            and event.get("correlationId") == task_id
            and event.get("eventType") == event_type
            and payload.get("intentIndex") == index
            and payload.get("robotId") == intent.get("robotId")
            and verdict.get("status") == intent.get("harnessStatus") == "SATISFIED"
            and verdict.get("reason")
            == intent.get("harnessReason")
            == "PHYSICAL_POSTCONDITIONS_SATISFIED"
            and verdict.get("evidenceIds") == evidence_ids
            and verdict.get("worldRevision") == intent.get("worldRevision")
            and isinstance(observations, list)
            and len(observations) == len(evidence_ids) == 2
            and {item.get("observationId") for item in observations} == set(evidence_ids)
            and transition.get("resourceId") == intent.get("resourceId") == "block:red-block"
            and transition.get("fromOwner") == intent.get("robotId")
            and transition.get("fromFencingToken") == intent.get("fencingToken")
            and transition.get("toOwner")
            == (intents[index + 1]["robotId"] if index == 0 else "environment")
            and transition.get("toFencingToken")
            == (intents[index + 1]["fencingToken"] if index == 0 else final_token)
        ):
            return False
        started = _parse_timestamp(intent.get("startedAt"))
        finished = _parse_timestamp(intent.get("finishedAt"))
        occurred = _parse_timestamp(event.get("occurredAt"))
        if (
            started is None
            or finished is None
            or occurred is None
            or not started < finished <= occurred
        ):
            return False
        by_source = {item.get("sourceId"): item for item in observations if isinstance(item, dict)}
        robot_observation = by_source.get(intent.get("robotSourceId"))
        entity_observations = [
            item
            for item in observations
            if isinstance(item, dict) and str(item.get("sourceId", "")).endswith("/scene")
        ]
        if len(entity_observations) != 1 or not isinstance(robot_observation, dict):
            return False
        for observation in observations:
            observed_at = _parse_timestamp(observation.get("observedAt"))
            sequence_text = observation.get("sourceSequence")
            observation_id = observation.get("observationId")
            try:
                parsed_source, parsed_sequence, parsed_nanos = str(observation_id).rsplit("/", 2)
                parsed_sequence = int(parsed_sequence)
                parsed_nanos = int(parsed_nanos)
            except (TypeError, ValueError):
                return False
            allowed_sources = {
                "robot-1/scene",
                "robot-2/scene",
                intent.get("robotSourceId"),
            }
            observed_millis = _timestamp_epoch_millis(observation.get("observedAt"))
            if (
                observed_at is None
                or not started < observed_at <= finished
                or not isinstance(sequence_text, str)
                or re.fullmatch(r"[1-9][0-9]*", sequence_text) is None
                or observation.get("sourceId") not in allowed_sources
                or parsed_source != observation.get("sourceId")
                or parsed_sequence != int(sequence_text)
                or parsed_nanos // 1_000_000 != observed_millis
                or observation.get("frameId") != "world"
                or observation.get("transformRevision") != "robocasa-world-v1"
            ):
                return False
            authoritative = []
            for sample in trajectory_samples:
                if not isinstance(sample, dict) or sample.get("revision", -1) < verdict.get(
                    "worldRevision", math.inf
                ):
                    continue
                authoritative.append(
                    sample.get("entities", {}).get("red-block", {}).get("evidence", {})
                )
                authoritative.extend(
                    robot.get("evidence", {})
                    for robot in sample.get("robots", {}).values()
                    if isinstance(robot, dict)
                )
            if not any(
                candidate.get("observationId") == observation_id
                and candidate.get("sourceId") == observation.get("sourceId")
                and str(candidate.get("sourceSequence")) == sequence_text
                and candidate.get("observedAt") == observation.get("observedAt")
                and candidate.get("frameId") == observation.get("frameId")
                and candidate.get("transformRevision") == observation.get("transformRevision")
                for candidate in authoritative
                if isinstance(candidate, dict)
            ):
                return False
        if int(robot_observation["sourceSequence"]) <= intent.get("robotSequenceBasis", -1):
            return False
        entity_observation = entity_observations[0]
        if entity_observation.get("sourceId") == intent.get("entitySourceId") and int(
            entity_observation["sourceSequence"]
        ) <= intent.get("entitySequenceBasis", -1):
            return False
    return True


def _safe_artifact(output: Path, relative: str) -> Path | None:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        return None
    try:
        resolved = (output / relative).resolve()
        resolved.relative_to(output.resolve())
    except ValueError:
        return None
    return resolved


def _valid_png(path: Path) -> bool:
    try:
        from PIL import Image

        payload = _read_audited_regular_file(path)
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG" or image.width < 1 or image.height < 1:
                return False
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
    except (OSError, SyntaxError, ValueError):
        return False
    return True


def _png_visual_metrics(path: Path, canvas_rect: dict | None) -> dict | None:
    try:
        from PIL import Image, ImageFilter, ImageStat

        payload = _read_audited_regular_file(path)
        with Image.open(io.BytesIO(payload)) as opened:
            image = opened.convert("RGB")
            if image.size != EXPECTED_VIEWPORT:
                return None
            sample = image.resize((176, 100))
            pixels = list(sample.get_flattened_data())
            non_black = sum(max(pixel) >= 18 for pixel in pixels) / len(pixels)
            colorful = sum(max(pixel) - min(pixel) >= 12 for pixel in pixels) / len(pixels)
            entropy = sample.convert("L").entropy()
            edges = ImageStat.Stat(sample.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
            gray = sample.convert("L")
            row_diversity = len(
                {gray.crop((0, y, gray.width, y + 1)).tobytes() for y in range(gray.height)}
            )
            column_diversity = len(
                {gray.crop((x, 0, x + 1, gray.height)).tobytes() for x in range(gray.width)}
            )
            if not isinstance(canvas_rect, dict):
                return None
            x = int(canvas_rect.get("x", -1))
            y = int(canvas_rect.get("y", -1))
            width = int(canvas_rect.get("width", 0))
            height = int(canvas_rect.get("height", 0))
            if x < 0 or y < 0 or width < 500 or height < 300:
                return None
            if x + width > image.width or y + height > image.height:
                return None
            canvas = image.crop((x, y, x + width, y + height)).resize((136, 50))
            canvas_entropy = canvas.convert("L").entropy()
            canvas_edges = ImageStat.Stat(canvas.convert("L").filter(ImageFilter.FIND_EDGES)).mean[
                0
            ]
    except (OSError, SyntaxError, TypeError, ValueError):
        return None
    return {
        "width": EXPECTED_VIEWPORT[0],
        "height": EXPECTED_VIEWPORT[1],
        "entropy": round(entropy, 4),
        "nonBlackRatio": round(non_black, 4),
        "colorfulRatio": round(colorful, 4),
        "edgeMean": round(edges, 4),
        "canvasEntropy": round(canvas_entropy, 4),
        "canvasEdgeMean": round(canvas_edges, 4),
        "rowDiversity": row_diversity,
        "columnDiversity": column_diversity,
        "substantial": (
            entropy >= 2.5
            and non_black >= 0.35
            and colorful >= 0.04
            and edges >= 2.0
            and canvas_entropy >= 1.8
            and canvas_edges >= 1.0
            and row_diversity >= 10
            and column_diversity >= 10
        ),
    }


def _visible_rect(record: dict, viewport: tuple[int, int], *, minimum_area: int = 100) -> bool:
    if not isinstance(record, dict) or record.get("visible") is not True:
        return False
    rect = record.get("rect")
    if not isinstance(rect, dict):
        return False
    values = [rect.get(key) for key in ("x", "y", "width", "height")]
    if not all(_real_number(value) for value in values):
        return False
    x, y, width, height = values
    return (
        x >= 0
        and y >= 0
        and width > 0
        and height > 0
        and width * height >= minimum_area
        and x + width <= viewport[0]
        and y + height <= viewport[1]
    )


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _timestamp_epoch_millis(value) -> int:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return -1
    delta = parsed - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def _snapshot_order_valid(initial: dict, moving: dict, final: dict) -> bool:
    revisions = [snapshot.get("revision") for snapshot in (initial, moving, final)]
    timestamps = [
        _parse_timestamp(snapshot.get("projectedAt")) for snapshot in (initial, moving, final)
    ]
    return (
        all(type(value) is int for value in revisions)
        and revisions[0] < revisions[1] < revisions[2]
        and all(value is not None for value in timestamps)
        and timestamps[0] < timestamps[1] < timestamps[2]
    )


def _runtime_browser_url_allowed(url: str, base_url: str, task_id: str) -> bool:
    parsed = urlsplit(url)
    if _origin(url) != _origin(base_url) or parsed.fragment:
        return False
    if parsed.path in RUNTIME_NO_QUERY_PATHS:
        return parsed.query == ""
    task_read_paths = {
        f"/v1/tasks/{task_id}/experience",
        f"/v1/tasks/{task_id}/intents",
        f"/v1/tasks/{task_id}/revisions",
    }
    if parsed.path in task_read_paths:
        return parsed.query == ""
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    if parsed.path == "/v1/telemetry":
        return len(query) == 2 and dict(query) == {"robot_id": "", "limit": "20"}
    if RUNTIME_ROBOT_FRAME_RE.fullmatch(parsed.path) is not None:
        return (
            len(query) == 1
            and query[0][0] == "t"
            and len(query[0][1]) == 13
            and query[0][1].isascii()
            and query[0][1].isdigit()
        )
    return False


def _browser_network_valid(
    network: dict | None,
    corroboration: dict | None,
    browser: dict | None,
    run_context: dict,
    task_id: str,
    manifest: dict,
) -> bool:
    if network is None or corroboration is None or browser is None:
        return False
    base_url = run_context.get("publicBaseUrl")
    if not isinstance(base_url, str):
        return False
    requests = network.get("requests")
    expected_page = base_url.rstrip("/") + f"/?acceptance_task={task_id}"
    manifest_url = base_url.rstrip("/") + "/assets/scenes/robocasa-handoff-v1/manifest.json"
    try:
        expected_urls = {
            "document": expected_page,
            "styles": base_url.rstrip("/") + "/styles.css",
            "webgl": base_url.rstrip("/") + "/webgl_scene.js",
            "world-view": base_url.rstrip("/") + "/world_view.js",
            "app": base_url.rstrip("/") + "/app.js",
            "manifest": manifest_url,
            "scene": urljoin(manifest_url, manifest["sceneAsset"]),
            "robot": urljoin(manifest_url, manifest["robotModels"]["xlerobot"]["asset"]),
            "binding": urljoin(manifest_url, manifest["robotModels"]["xlerobot"]["binding"]),
        }
        expected_build = _expected_frontend_build()
    except (KeyError, OSError, TypeError, ValueError):
        return False
    if not isinstance(requests, list) or len(requests) != len(NETWORK_ROLES):
        return False
    raw_urls = [item.get("url") for item in requests if isinstance(item, dict)]
    build_resources = expected_build["resources"]
    browser_inventory = network.get("browserInventory")
    observed_inventory_urls = (
        browser_inventory.get("observedURLs") if isinstance(browser_inventory, dict) else None
    )
    required_urls = set(expected_urls.values())
    inventory_valid = (
        isinstance(browser_inventory, dict)
        and browser_inventory.get("schemaVersion") == "tangying.browser-request-inventory.v1"
        and browser_inventory.get("pageUrl") == expected_page
        and browser_inventory.get("capturedBy") == "browser-page-assets"
        and isinstance(observed_inventory_urls, list)
        and all(isinstance(url, str) for url in observed_inventory_urls)
        and len(observed_inventory_urls) == len(set(observed_inventory_urls))
        and required_urls.issubset(set(observed_inventory_urls))
        and all(_origin(url) == _origin(base_url) for url in observed_inventory_urls)
        and network.get("browserInventoryDigest") == canonical_digest(browser_inventory)
    )
    if inventory_valid:
        unexpected = []
        for url in observed_inventory_urls:
            if url in required_urls:
                continue
            if _runtime_browser_url_allowed(url, base_url, task_id):
                continue
            unexpected.append(url)
        inventory_valid = unexpected == network.get("unexpectedRequests") == []
    raw_valid = len(raw_urls) == len(requests)
    for item, role, resource in zip(requests, NETWORK_ROLES, build_resources, strict=True):
        raw_valid = raw_valid and (
            isinstance(item, dict)
            and item.get("role") == role
            and item.get("url") == expected_urls[role]
            and item.get("responseUrl") == expected_urls[role]
            and item.get("method") == "GET"
            and item.get("status") == 200
            and _origin(item["url"]) == _origin(base_url)
            and _origin(item["responseUrl"]) == _origin(base_url)
            and item.get("requestHeaders", {}).get("Cache-Control") == "no-cache, no-store"
            and item.get("responseHeaders", {}).get("X-Tangying-Acceptance-Nonce")
            == run_context.get("episodeNonce")
            and item.get("observedBy") == "runner-server-corroboration"
            and item.get("bytes") == resource["bytes"]
            and item.get("sha256") == resource["sha256"]
        )
    corroboration_valid = (
        corroboration.get("schemaVersion") == "tangying.runner-network-corroboration.v1"
        and corroboration.get("runId") == run_context.get("runId")
        and corroboration.get("episodeNonce") == run_context.get("episodeNonce")
        and corroboration.get("taskId") == task_id
        and corroboration.get("pageUrl") == expected_page
        and corroboration.get("baseOrigin")
        == f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
        and corroboration.get("observedRequestCount") == len(requests)
        and corroboration.get("observedURLs") == raw_urls
        and corroboration.get("externalOrigins") == []
        and corroboration.get("sameOrigin") is True
        and corroboration.get("cacheDisabled") is True
        and corroboration.get("inventorySource") == "runner-corroboration-only"
        and corroboration.get("requests") == requests
    )
    return (
        network.get("schemaVersion") == "tangying.browser-network.v2"
        and network.get("runId") == run_context.get("runId")
        and network.get("episodeNonce") == run_context.get("episodeNonce")
        and network.get("taskId") == task_id
        and network.get("pageUrl") == expected_page
        and network.get("baseOrigin")
        == f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
        and network.get("inventorySource") == "controlled-browser-server-responses"
        and network.get("inventoryComplete") is True
        and type(network.get("observedRequestCount")) is int
        and network["observedRequestCount"] == len(requests)
        and network.get("observedURLs") == raw_urls
        and [item.get("role") for item in requests] == list(NETWORK_ROLES)
        and network.get("unexpectedRequests") == []
        and network.get("cacheDisabled") is True
        and network.get("externalOrigins") == []
        and network.get("sameOrigin") is True
        and network.get("frontendBuild") == expected_build
        and inventory_valid
        and browser.get("networkDigest") == canonical_digest(network)
        and raw_valid
        and corroboration_valid
    )


def _browser_performance_valid(performance: dict | None, run_context: dict, task_id: str) -> bool:
    if performance is None:
        return False
    interactions = performance.get("interactions", {})
    raw = performance.get("raw", {})
    required_interactions = (
        "fourIndependentToggles",
        "leftPanChangedFrame",
        "pointerZoomChangedFrame",
        "resetChangedFrame",
        "topPresetChangedFrame",
        "followEnabled",
        "panCancelledFollow",
        "selectedAndFocusedRobot2",
        "refreshRestoredCamera",
        "cachedOfflineCameraInteractive",
    )
    page_started = raw.get("pageStartedAtMs")
    events = raw.get("interactionEvents")
    frame_times = raw.get("frameTimesMs")
    render_durations = raw.get("renderDurationMs")
    render_timestamps = raw.get("renderDurationTimestampsMs")
    page_time_origin = raw.get("pageTimeOriginMs")
    readiness = raw.get("readinessSamples")
    refresh_started = raw.get("refreshStartedAtMs")
    refresh_readiness = raw.get("refreshReadinessSamples")
    if not (
        _real_number(page_started)
        and isinstance(events, list)
        and isinstance(frame_times, list)
        and len(frame_times) >= 600
        and all(_real_number(value) for value in frame_times)
        and all(left < right for left, right in pairwise(frame_times))
        and isinstance(render_durations, list)
        and len(render_durations) >= 120
        and all(_real_number(value) and value >= 0 for value in render_durations)
        and isinstance(render_timestamps, list)
        and len(render_timestamps) == len(render_durations)
        and all(_real_number(value) for value in render_timestamps)
        and len(set(render_timestamps)) >= 120
        and max(render_timestamps) - min(render_timestamps) >= 1_000
        and _real_number(page_time_origin)
        and isinstance(readiness, list)
        and isinstance(refresh_readiness, list)
        and _real_number(refresh_started)
    ):
        return False
    raw_interactions = {
        event.get("name"): event
        for event in events
        if isinstance(event, dict) and isinstance(event.get("name"), str)
    }
    if not events or not all(
        name in raw_interactions
        and raw_interactions[name].get("passed") is True
        and _real_number(raw_interactions[name].get("atMs"))
        for name in required_interactions
    ):
        return False
    first_interaction_ms = min(event["atMs"] for event in raw_interactions.values()) - page_started
    steady_window = frame_times[-121:] if len(frame_times) >= 121 else frame_times
    steady_fps = (len(steady_window) - 1) * 1000 / (steady_window[-1] - steady_window[0])
    frame_deltas = [right - left for left, right in pairwise(frame_times)]
    authentic_jitter = max(frame_deltas) - min(frame_deltas) >= 0.01
    last_interaction_at = max(event["atMs"] for event in raw_interactions.values())
    non_interaction_pairs = [
        (timestamp, duration)
        for timestamp, duration in zip(render_timestamps, render_durations, strict=True)
        if page_time_origin + timestamp >= last_interaction_at + 250
    ]
    if len(non_interaction_pairs) < 120:
        return False
    steady_pairs = non_interaction_pairs[-min(300, len(non_interaction_pairs)) :]
    steady_render_durations = [duration for _, duration in steady_pairs]
    sorted_durations = sorted(steady_render_durations)
    render_mean_ms = sum(steady_render_durations) / len(steady_render_durations)
    render_median_ms = sorted_durations[math.ceil(len(sorted_durations) * 0.50) - 1]
    render_p90_ms = sorted_durations[math.ceil(len(sorted_durations) * 0.90) - 1]
    render_p95_ms = sorted_durations[math.ceil(len(sorted_durations) * 0.95) - 1]
    render_max_ms = sorted_durations[-1]
    render_capacity_fps = 1000 / render_mean_ms if render_mean_ms > 0 else math.inf
    duration_jitter = max(render_durations) - min(render_durations) >= 0.01
    ready_at = next(
        (
            sample.get("atMs")
            for sample in readiness
            if isinstance(sample, dict)
            and sample.get("worldStatus") == "LIVE"
            and sample.get("visualStatus") == "LIVE"
            and _real_number(sample.get("atMs"))
        ),
        None,
    )
    refresh_ready_at = next(
        (
            sample.get("atMs")
            for sample in refresh_readiness
            if isinstance(sample, dict)
            and sample.get("worldStatus") == "LIVE"
            and sample.get("visualStatus") == "LIVE"
            and _real_number(sample.get("atMs"))
            and sample["atMs"] >= refresh_started
        ),
        None,
    )
    if ready_at is None or refresh_ready_at is None:
        return False
    refresh_ms = refresh_ready_at - refresh_started
    return (
        performance.get("schemaVersion") == "tangying.browser-performance.v1"
        and performance.get("runId") == run_context.get("runId")
        and performance.get("episodeNonce") == run_context.get("episodeNonce")
        and performance.get("taskId") == task_id
        and performance.get("browserMeasured") is True
        and 0 <= ready_at - page_started <= 5000
        and 0 <= first_interaction_ms <= 5000
        and abs(performance.get("firstInteractionMs", math.inf) - first_interaction_ms) < 1
        and authentic_jitter
        and abs(performance.get("steadyFps", math.inf) - steady_fps) < 0.2
        and duration_jitter
        and render_capacity_fps >= 50
        and abs(performance.get("renderCapacityFps", -math.inf) - render_capacity_fps) < 0.2
        and abs(performance.get("renderDurationMeanMs", math.inf) - render_mean_ms) < 0.02
        and abs(performance.get("renderDurationMedianMs", math.inf) - render_median_ms) < 0.02
        and abs(performance.get("renderDurationP90Ms", math.inf) - render_p90_ms) < 0.02
        and abs(performance.get("renderDurationP95Ms", math.inf) - render_p95_ms) < 0.02
        and abs(performance.get("renderDurationMaxMs", math.inf) - render_max_ms) < 0.02
        and performance.get("renderSteadySampleCount") == len(steady_pairs)
        and abs(performance.get("renderSteadyWindowStartedAtMs", math.inf) - steady_pairs[0][0])
        < 0.02
        and abs(performance.get("renderSteadyWindowEndedAtMs", math.inf) - steady_pairs[-1][0])
        < 0.02
        and 0 <= refresh_ms <= 5000
        and abs(performance.get("refreshRecoveryMs", math.inf) - refresh_ms) < 1
        and all(interactions.get(name) is True for name in required_interactions)
        and "deterministic" in str(interactions.get("rightOrbit", ""))
        and "blocked" in str(interactions.get("fileMode", ""))
    )


def _asset_evidence_valid(
    output: Path,
    run_context: dict,
    task_id: str,
    manifest: dict,
    visual: dict,
) -> tuple[bool, bool]:
    saved_manifest = _load_json(output / "visual-manifest.json")
    network = _load_json(output / "visual-asset-network.json")
    base_url = run_context.get("publicBaseUrl")
    if saved_manifest != manifest or network is None or not isinstance(base_url, str):
        return False, False
    base_origin = f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
    manifest_url = base_url.rstrip("/") + "/assets/scenes/robocasa-handoff-v1/manifest.json"
    try:
        references = [
            manifest["sceneAsset"],
            manifest["robotModels"]["xlerobot"]["asset"],
            manifest["robotModels"]["xlerobot"]["binding"],
        ]
        expected_urls = [urljoin(manifest_url, reference) for reference in references]
        content_hashes = manifest["contentHashes"]
    except (KeyError, TypeError):
        return False, False
    requests = network.get("requests")
    provenance = (
        network.get("schemaVersion") == "tangying.asset-network.v1"
        and network.get("runId") == run_context.get("runId")
        and network.get("episodeNonce") == run_context.get("episodeNonce")
        and network.get("taskId") == task_id
        and network.get("baseOrigin") == base_origin
        and isinstance(requests, list)
        and len(requests) == len(expected_urls)
    )
    hashes_valid = provenance and visual.get("assetHashesMatch") is True
    origins_valid = provenance and visual.get("sameOrigin") is True
    if not isinstance(requests, list) or len(requests) != len(expected_urls):
        return False, False
    for item, expected_url in zip(requests, expected_urls, strict=True):
        filename = urlsplit(expected_url).path.rsplit("/", 1)[-1]
        expected_hash = content_hashes.get(filename)
        item_hash = item.get("sha256")
        item_origin = item.get("origin")
        origins_valid = origins_valid and (
            _origin(expected_url) == _origin(base_url)
            and item.get("url") == expected_url
            and item_origin == base_origin
            and _origin(str(item.get("url", ""))) == _origin(base_url)
        )
        hashes_valid = hashes_valid and (
            isinstance(expected_hash, str)
            and SHA256_RE.fullmatch(expected_hash) is not None
            and isinstance(item_hash, str)
            and SHA256_RE.fullmatch(item_hash) is not None
            and item_hash == expected_hash
            and item.get("expectedSha256") == expected_hash
            and item.get("hashMatches") is True
            and type(item.get("bytes")) is int
            and item["bytes"] > 0
        )
    origins_valid = origins_valid and (
        network.get("externalOrigins") == [] and network.get("sameOrigin") is True
    )
    return bool(hashes_valid), bool(origins_valid)


def _provenance_and_screenshots(
    output: Path,
    run_context: dict,
    task_id: str,
    initial_world: dict,
    moving_world: dict,
    world: dict,
) -> tuple[bool, bool, dict, dict | None, dict | None]:
    browser = _load_json(output / "browser-evidence.json")
    network = _load_json(output / "visual-network.json")
    performance = _load_json(output / "visual-performance.json")
    run_file = _load_json(output / "run-context.json")
    provenance = (
        browser is not None
        and run_file == run_context
        and RUN_ID_RE.fullmatch(str(run_context.get("runId", ""))) is not None
        and NONCE_RE.fullmatch(str(run_context.get("episodeNonce", ""))) is not None
        and browser.get("schemaVersion") == "tangying.browser-acceptance.v1"
        and browser.get("runId") == run_context.get("runId")
        and browser.get("episodeNonce") == run_context.get("episodeNonce")
        and browser.get("taskId") == task_id
        and browser.get("request") == HANDOFF_PROMPT
        and browser.get("adapter") == "robocasa"
        and browser.get("sceneId") == "robocasa-handoff-v1"
        and browser.get("contextDigest") == canonical_digest(run_context)
        and browser.get("receiverAuthenticated") is True
    )
    expected_worlds = {"initial": initial_world, "moving": moving_world, "final": world}
    for name, snapshot in expected_worlds.items():
        record = run_context.get("snapshots", {}).get(name, {})
        provenance = provenance and (
            record.get("revision") == snapshot.get("revision")
            and record.get("projectedAt") == snapshot.get("projectedAt")
            and record.get("digest") == canonical_digest(snapshot)
        )
    screenshot_valid = provenance
    screenshot_metadata: dict[str, dict] = {}
    if browser is None:
        return False, False, screenshot_metadata, network, performance
    if (
        set(browser.get("snapshots", {})) != set(VISUAL_SCREENSHOTS)
        or set(browser.get("screenshots", {})) != set(VISUAL_SCREENSHOTS)
        or set(browser.get("captures", {})) != set(VISUAL_SCREENSHOTS)
    ):
        return False, False, screenshot_metadata, network, performance
    for name in VISUAL_SCREENSHOTS:
        snapshot_record = browser["snapshots"][name]
        screenshot_record = browser["screenshots"][name]
        capture = browser["captures"][name]
        snapshot_path = _safe_artifact(output, snapshot_record.get("path"))
        screenshot_path = _safe_artifact(output, screenshot_record.get("path"))
        snapshot = _load_json(snapshot_path)
        snapshot_digest = canonical_digest(snapshot) if snapshot is not None else ""
        try:
            screenshot_payload = (
                _read_audited_regular_file(screenshot_path) if screenshot_path is not None else b""
            )
        except (OSError, ValueError):
            screenshot_payload = b""
        statuses_valid = screenshot_record.get("worldStatus") == "LIVE" and screenshot_record.get(
            "visualStatus"
        ) == ("DEGRADED" if name == "fallback" else "LIVE")
        viewport = capture.get("viewport", {})
        viewport_tuple = (viewport.get("width"), viewport.get("height"))
        document = capture.get("document", {})
        marker = document.get("marker", {})
        world_badge = document.get("worldBadge", {})
        visual_badge = document.get("visualBadge", {})
        canvas = document.get("canvas", {})
        canvas_region = canvas.get("visibleRect", canvas.get("rect"))
        expected_visual = "VISUAL DEGRADED" if name == "fallback" else "VISUAL LIVE"
        expected_canvas = "fleet-godview-canvas" if name == "fallback" else "fleet-godview-webgl"
        expected_marker = f"ACCEPT {run_context.get('episodeNonce')} · TASK {task_id} · REV {snapshot.get('revision') if snapshot else ''}"
        dom_valid = (
            capture.get("captureName") == name
            and _parse_timestamp(capture.get("capturedAt")) is not None
            and capture.get("capturedAt") == screenshot_record.get("capturedAt")
            and capture.get("episodeNonce") == run_context.get("episodeNonce")
            and capture.get("taskId") == task_id
            and capture.get("worldRevision") == (snapshot or {}).get("revision")
            and capture.get("worldDigest") == snapshot_digest
            and viewport_tuple == EXPECTED_VIEWPORT
            and document.get("acceptanceNonce") == run_context.get("episodeNonce")
            and marker.get("text") == expected_marker
            and _visible_rect(marker, EXPECTED_VIEWPORT, minimum_area=400)
            and world_badge.get("text") == "WORLD LIVE"
            and _visible_rect(world_badge, EXPECTED_VIEWPORT)
            and visual_badge.get("text") == expected_visual
            and _visible_rect(visual_badge, EXPECTED_VIEWPORT)
            and canvas.get("id") == expected_canvas
            and _visible_rect(
                {"visible": canvas.get("visible"), "rect": canvas_region},
                EXPECTED_VIEWPORT,
                minimum_area=150000,
            )
        )
        visual_metrics = (
            _png_visual_metrics(screenshot_path, canvas_region)
            if screenshot_path is not None
            else None
        )
        record_valid = (
            snapshot is not None
            and snapshot.get("schemaVersion") == "world.snapshot.v1"
            and snapshot.get("worldId") == "robocasa-handoff-v1"
            and snapshot.get("acceptanceNonce") == run_context.get("episodeNonce")
            and type(snapshot.get("revision")) is int
            and snapshot["revision"] >= world.get("revision", -1)
            and snapshot_record.get("revision") == snapshot["revision"]
            and snapshot_record.get("projectedAt") == snapshot.get("projectedAt")
            and snapshot_record.get("digest") == snapshot_digest
            and screenshot_record.get("path") == f"visual/{name}.png"
            and screenshot_record.get("format") == "png"
            and screenshot_record.get("sha256") == hashlib.sha256(screenshot_payload).hexdigest()
            and screenshot_record.get("bytes") == len(screenshot_payload)
            and screenshot_record.get("worldRevision") == snapshot["revision"]
            and screenshot_record.get("worldDigest") == snapshot_digest
            and screenshot_record.get("episodeNonce") == run_context.get("episodeNonce")
            and screenshot_record.get("taskId") == task_id
            and statuses_valid
            and dom_valid
            and screenshot_path is not None
            and screenshot_path.suffix == ".png"
            and _valid_png(screenshot_path)
            and visual_metrics is not None
            and visual_metrics.get("substantial") is True
        )
        if name in {"handoff-final", "fallback"}:
            record_valid = record_valid and (
                snapshot.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
                == "right-target-zone"
                and _final_held_clear(snapshot)
                and _source_freshness_valid(snapshot)
            )
        provenance = provenance and record_valid
        screenshot_valid = screenshot_valid and record_valid
        screenshot_metadata[name] = {**screenshot_record, "verifiedVisualMetrics": visual_metrics}
    return provenance, screenshot_valid, screenshot_metadata, network, performance


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise AssertionError(f"runner network redirect rejected: {request.full_url} -> {new_url}")


def _fetch_network_lifecycle(
    urls: list[tuple[str, str]], *, base_url: str, episode_nonce: str
) -> list[dict]:
    opener = build_opener(_NoRedirect())
    records = []
    for role, url in urls:
        if _origin(url) != _origin(base_url):
            raise AssertionError("runner network origin mismatch")
        request_headers = {"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}
        response = opener.open(Request(url, headers=request_headers), timeout=10)
        try:
            payload = response.read()
            final_url = response.geturl()
            headers = {key: value for key, value in response.headers.items()}
            status = response.status
        finally:
            response.close()
        if final_url != url or _origin(final_url) != _origin(base_url):
            raise AssertionError("runner network final URL mismatch")
        if headers.get("X-Tangying-Acceptance-Nonce") != episode_nonce:
            raise AssertionError("runner network response nonce mismatch")
        records.append(
            {
                "role": role,
                "url": url,
                "method": "GET",
                "requestHeaders": request_headers,
                "status": status,
                "responseUrl": final_url,
                "responseHeaders": headers,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return records


def collect_visual_evidence(
    stack,
    world: dict,
    output: Path,
    *,
    run_id: str,
    task_id: str,
    episode_nonce: str,
    public_base_url: str | None = None,
) -> tuple[dict, dict]:
    scene_id, _model_hash, _adapter = _model_identity(world)
    base_url = (public_base_url or stack.base_url).rstrip("/")
    manifest_path = f"/assets/scenes/{scene_id}/manifest.json"
    manifest_url = base_url + manifest_path
    manifest = (
        stack.public_json(manifest_path)
        if public_base_url is None
        else json.loads(stack.public_bytes(manifest_url, base_url=base_url))
    )
    base_origin = f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
    references = [
        manifest["sceneAsset"],
        manifest["robotModels"]["xlerobot"]["asset"],
        manifest["robotModels"]["xlerobot"]["binding"],
    ]
    urls = [urljoin(manifest_url, reference) for reference in references]
    if any(_origin(url) != _origin(base_url) for url in urls):
        raise AssertionError("visual asset reference origin mismatch")
    page_url = base_url + f"/?acceptance_task={task_id}"
    lifecycle_urls = [
        page_url,
        base_url + "/styles.css",
        base_url + "/webgl_scene.js",
        base_url + "/world_view.js",
        base_url + "/app.js",
        manifest_url,
        *urls,
    ]
    lifecycle = _fetch_network_lifecycle(
        list(zip(NETWORK_ROLES, lifecycle_urls, strict=True)),
        base_url=base_url,
        episode_nonce=episode_nonce,
    )
    runner_network = {
        "schemaVersion": "tangying.runner-network-corroboration.v1",
        "runId": run_id,
        "episodeNonce": episode_nonce,
        "taskId": task_id,
        "capturedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "pageUrl": page_url,
        "baseOrigin": base_origin,
        "observedRequestCount": len(lifecycle),
        "observedURLs": [item["url"] for item in lifecycle],
        "externalOrigins": [],
        "sameOrigin": True,
        "cacheDisabled": True,
        "inventorySource": "runner-corroboration-only",
        "requests": lifecycle,
    }
    for item in runner_network["requests"]:
        item["observedBy"] = "runner-server-corroboration"
    write_json(output / "visual-network-corroboration.json", runner_network)
    requests = []
    started = time.monotonic()
    for url, lifecycle_record in zip(urls, lifecycle[-3:], strict=True):
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        digest = lifecycle_record["sha256"]
        expected = manifest["contentHashes"][filename]
        requests.append(
            {
                "url": url,
                "origin": f"{urlsplit(url).scheme}://{urlsplit(url).netloc}",
                "bytes": lifecycle_record["bytes"],
                "sha256": digest,
                "expectedSha256": expected,
                "hashMatches": digest == expected,
            }
        )
    asset_network = {
        "schemaVersion": "tangying.asset-network.v1",
        "runId": run_id,
        "episodeNonce": episode_nonce,
        "taskId": task_id,
        "baseOrigin": base_origin,
        "requests": requests,
        "externalOrigins": [],
        "sameOrigin": all(item["origin"] == base_origin for item in requests),
        "publicAssetFetchMs": round((time.monotonic() - started) * 1000, 3),
    }
    write_json(output / "visual-manifest.json", manifest)
    write_json(output / "visual-asset-network.json", asset_network)
    return manifest, {
        "assetHashesMatch": all(item["hashMatches"] for item in requests),
        "sameOrigin": asset_network["sameOrigin"],
        "files": {
            "manifest": "visual-manifest.json",
            "assetNetwork": "visual-asset-network.json",
            "network": "visual-network.json",
            "performance": "visual-performance.json",
            "browser": "browser-evidence.json",
        },
    }


def build_acceptance_summary(
    *,
    output: Path,
    run_context: dict,
    task_id: str,
    task: dict,
    initial_world: dict,
    moving_world: dict,
    world: dict,
    manifest: dict,
    intents: list[dict],
    visual: dict,
    trusted_anchor_path: Path = TRUSTED_ANCHOR_PATH,
) -> dict:
    scene_valid = _scene_identity_valid(run_context, world, manifest)
    canonical_valid = _canonical_joints_valid(world)
    movement_valid = _joint_movement_valid(initial_world, moving_world)
    task_valid = _task_identity_valid(run_context, task_id, task)
    intents_valid = _intents_valid(intents)
    final_placement = (
        world.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
        == "right-target-zone"
    )
    held_valid = _final_held_clear(world)
    sources_valid = _source_freshness_valid(world)
    custody_valid = _custody_valid(world, intents)
    snapshot_order = _snapshot_order_valid(initial_world, moving_world, world)
    episode_nonce = run_context.get("episodeNonce")
    nonce_valid = NONCE_RE.fullmatch(str(episode_nonce or "")) is not None and all(
        snapshot.get("acceptanceNonce") == episode_nonce
        for snapshot in (initial_world, moving_world, world)
    )
    trajectory = _load_json(output / "world-trajectory.json")
    events = _load_json_list(output / "events.json")
    custody_trajectory = _custody_trajectory_valid(trajectory, run_context, task_id, intents, world)
    harness_evidence = _harness_evidence_valid(events, task_id, intents, world, trajectory)
    asset_hashes, asset_origin = _asset_evidence_valid(
        output, run_context, task_id, manifest, visual
    )
    provenance, screenshots, screenshot_metadata, network, performance = (
        _provenance_and_screenshots(
            output, run_context, task_id, initial_world, moving_world, world
        )
    )
    browser = _load_json(output / "browser-evidence.json")
    task_update = _load_json(output / "task-update.json")
    corroboration = _load_json(output / "visual-network-corroboration.json")
    checks = {
        "captureAuthentication": _authenticated_capture_valid(
            output, run_context, task_id, trusted_anchor_path
        ),
        "taskIdentity": task_valid,
        "sceneIdentity": scene_valid,
        "modelIdentity": scene_valid,
        "canonicalJoints": canonical_valid,
        "jointMovement": movement_valid,
        "intents": intents_valid,
        "finalPlacement": final_placement,
        "finalHeldClear": held_valid,
        "sourceFreshness": sources_valid,
        "custody": custody_valid,
        "snapshotOrder": snapshot_order,
        "episodeNonce": nonce_valid,
        "custodyTrajectory": custody_trajectory,
        "harnessEvidence": harness_evidence,
        "assetContentHashes": asset_hashes,
        "assetSameOrigin": asset_origin,
        "provenance": provenance,
        "screenshots": screenshots,
        "browserNetwork": _browser_network_valid(
            network, corroboration, browser, run_context, task_id, manifest
        ),
        "browserPerformance": _browser_performance_valid(performance, run_context, task_id),
        "versionedTaskUpdate": _versioned_task_update_valid(
            task_update, run_context, task, browser
        ),
    }
    policy_required = run_context.get("schemaVersion") == "tangying.robocasa-acceptance-run.v3"
    if policy_required:
        final_experience = (
            task_update.get("finalExperience") if isinstance(task_update, dict) else None
        )
        checks["policyToolEvidence"] = _policy_tool_evidence_valid(final_experience)
    return {
        "schemaVersion": (
            "tangying.robocasa-acceptance-summary.v6"
            if policy_required
            else "tangying.robocasa-acceptance-summary.v5"
        ),
        "runId": run_context.get("runId"),
        "episodeNonce": episode_nonce,
        "taskId": task_id,
        "state": task.get("state"),
        "passed": all(value is True for value in checks.values()),
        "sceneId": run_context.get("sceneId"),
        "modelRevision": manifest.get("modelHash"),
        "checks": checks,
        "snapshotDigests": run_context.get("snapshots"),
        "visualEvidence": visual.get("files", {}),
        "screenshots": screenshot_metadata,
        "taskUpdate": {
            "originalRequest": run_context.get("request"),
            "updateRequest": task_update.get("updateRequest")
            if isinstance(task_update, dict)
            else None,
            "finalRevision": task.get("currentRevision"),
        },
    }


def _policy_tool_evidence_valid(experience: object) -> bool:
    if not isinstance(experience, dict):
        return False
    activities = experience.get("activities")
    professional = experience.get("professional")
    if not isinstance(activities, list) or not isinstance(professional, dict):
        return False
    professional_activities = professional.get("activities")
    if not isinstance(professional_activities, list):
        return False
    confirmed = [
        activity
        for activity in activities
        if isinstance(activity, dict)
        and activity.get("controlMethod") == "仿真确定性策略"
        and activity.get("controlStage") == "已由环境确认"
    ]
    evidence = [
        activity.get("policy")
        for activity in professional_activities
        if isinstance(activity, dict) and isinstance(activity.get("policy"), dict)
    ]
    inference_ids = {
        item.get("inferenceId") for item in evidence if isinstance(item.get("inferenceId"), str)
    }
    wire = json.dumps(experience, ensure_ascii=False)
    return bool(
        len(confirmed) == 4
        and len(inference_ids) == 4
        and evidence
        and all(
            item.get("policyId") == "tangying-simulation-handoff"
            and item.get("manifestRevision")
            and item.get("observationId")
            for item in evidence
        )
        and "action_chunk" not in wire
        and "left_arm_gripper.pos" not in wire
    )


def _versioned_task_update_valid(
    evidence: object, run_context: object, task: object, browser: object
) -> bool:
    if not all(isinstance(value, dict) for value in (evidence, run_context, task, browser)):
        return False
    proposal_wrapper = evidence.get("proposal")
    confirmation = evidence.get("confirmation")
    waiting = evidence.get("waitingExperience")
    final = evidence.get("finalExperience")
    history = evidence.get("revisionHistory")
    browser_update = browser.get("taskUpdate")
    if not all(
        isinstance(value, dict)
        for value in (proposal_wrapper, confirmation, waiting, final, history, browser_update)
    ):
        return False
    proposal = proposal_wrapper.get("proposal")
    confirmed = confirmation.get("revision")
    professional = final.get("professional")
    if not all(isinstance(value, dict) for value in (proposal, confirmed, professional)):
        return False
    revision = proposal.get("revision")
    change = revision.get("changeSet") if isinstance(revision, dict) else None
    step_evidence = professional.get("stepEvidence")
    steps = final.get("steps")
    revisions = history.get("revisions")
    update_request = evidence.get("updateRequest")
    return bool(
        evidence.get("schemaVersion") == "tangying.robocasa-task-update.v1"
        and evidence.get("taskId") == run_context.get("taskId") == task.get("id")
        and evidence.get("originalRequest") == run_context.get("request") == HANDOFF_PROMPT
        and update_request == TASK_UPDATE_PROMPT == task.get("request")
        and isinstance(revision, dict)
        and revision.get("taskId") == task.get("id")
        and revision.get("revision") == 2
        and revision.get("baseRevision") == 1
        and revision.get("request") == update_request
        and proposal.get("status") == "PROPOSED"
        and isinstance(change, dict)
        and len(change.get("retained", [])) >= 1
        and len(change.get("changed", [])) >= 1
        and confirmed.get("status") == "WAITING_SAFE_POINT"
        and waiting.get("revision") == 2
        and waiting.get("updateStatus") == "WAITING_SAFE_POINT"
        and final.get("schemaVersion") == "task.experience.v1"
        and final.get("taskId") == task.get("id")
        and final.get("revision") == task.get("currentRevision") == 2
        and final.get("updateStatus") == "ACTIVE"
        and isinstance(steps, list)
        and len(steps) == 2
        and all(isinstance(step, dict) and step.get("status") == "SATISFIED" for step in steps)
        and isinstance(step_evidence, list)
        and any(
            isinstance(item, dict)
            and isinstance(item.get("evidenceIds"), list)
            and bool(item["evidenceIds"])
            for item in step_evidence
        )
        and history.get("currentRevision") == 2
        and isinstance(revisions, list)
        and len(revisions) == 2
        and task.get("state") == "SUCCEEDED"
        and browser_update.get("updateRequest") == update_request
        and browser_update.get("previewVisible") is True
        and browser_update.get("waitingSafePointVisible") is True
        and browser_update.get("finalRevision") == 2
    )


def _snapshot_record(world: dict) -> dict:
    return {
        "revision": world.get("revision"),
        "projectedAt": world.get("projectedAt"),
        "digest": canonical_digest(world),
    }


def _rebuild_retained_summary(output: Path, anchor_path: Path) -> dict | None:
    run_context = _load_json(output / "run-context.json")
    task = _load_json(output / "task.json")
    initial = _load_json(output / "world-initial.json")
    moving = _load_json(output / "world-moving.json")
    final = _load_json(output / "world-final.json")
    manifest = _load_json(output / "visual-manifest.json")
    intent_document = _load_json(output / "intents.json")
    asset_network = _load_json(output / "visual-asset-network.json")
    saved_summary = _load_json(output / "summary.json")
    if not all(
        isinstance(value, dict)
        for value in (
            run_context,
            task,
            initial,
            moving,
            final,
            manifest,
            intent_document,
            asset_network,
            saved_summary,
        )
    ):
        return None
    intents = intent_document.get("intents")
    requests = asset_network.get("requests")
    if not isinstance(intents, list) or not isinstance(requests, list):
        return None
    visual = {
        "assetHashesMatch": bool(requests)
        and all(isinstance(item, dict) and item.get("hashMatches") is True for item in requests),
        "sameOrigin": asset_network.get("sameOrigin") is True,
        "files": saved_summary.get("visualEvidence", {}),
    }
    return build_acceptance_summary(
        output=output,
        run_context=run_context,
        task_id=run_context.get("taskId"),
        task=task,
        initial_world=initial,
        moving_world=moving,
        world=final,
        manifest=manifest,
        intents=intents,
        visual=visual,
        trusted_anchor_path=anchor_path,
    )


def validate_retained_pack(output: Path, anchor_path: Path) -> bool:
    """Verify the pinned final signature and recompute every acceptance check."""
    if not isinstance(output, FDRootedDirectory):
        try:
            with _held_evidence_root(output) as rooted:
                rooted_anchor = _path_from_held_root(rooted, anchor_path)
                return validate_retained_pack(rooted, rooted_anchor)
        except (OSError, ValueError):
            return False
    output = output.resolve()
    try:
        actual_files = _attestation_files(output)
    except (OSError, ValueError):
        return False
    attestation_path = output / "acceptance-attestation.json"
    summary_path = output / "summary.json"
    envelope_path = output / "capture-envelope.json"
    attestation = _load_json(attestation_path)
    anchor = _load_json(anchor_path)
    candidate_anchor = _load_json(output / "capture-anchor-candidate.json")
    summary = _load_json(summary_path)
    envelope = _load_json(envelope_path)
    run_context = _load_json(output / "run-context.json")
    if not all(
        isinstance(value, dict)
        for value in (
            attestation,
            anchor,
            candidate_anchor,
            summary,
            envelope,
            run_context,
        )
    ):
        return False
    if (output / "capture-session.json").exists():
        return False
    public_pem = attestation.get("publicKeyPem")
    fingerprint = _public_key_fingerprint(public_pem)
    files = attestation.get("files")
    try:
        attestation_hash = _sha256_audited_regular_file(attestation_path)
        summary_hash = _sha256_audited_regular_file(summary_path)
        envelope_hash = _sha256_audited_regular_file(envelope_path)
    except (OSError, ValueError):
        return False
    identity_valid = (
        attestation.get("schemaVersion") == "tangying.robocasa-acceptance-attestation.v1"
        and anchor.get("schemaVersion") == "tangying.trusted-acceptance-anchor.v2"
        and attestation.get("runId") == anchor.get("runId") == run_context.get("runId")
        and attestation.get("episodeNonce")
        == anchor.get("episodeNonce")
        == run_context.get("episodeNonce")
        and attestation.get("taskId") == anchor.get("taskId") == run_context.get("taskId")
        and public_pem == anchor.get("publicKeyPem")
        and public_pem == envelope.get("publicKeyPem")
        and fingerprint
        == attestation.get("publicKeyFingerprint")
        == anchor.get("publicKeyFingerprint")
        == envelope.get("publicKeyFingerprint")
        and attestation_hash == anchor.get("attestationSha256")
        and _canonical_bytes(candidate_anchor) == _canonical_bytes(anchor)
        and summary_hash == attestation.get("summarySha256") == anchor.get("summarySha256")
        and envelope_hash
        == attestation.get("captureEnvelopeSha256")
        == anchor.get("captureEnvelopeSha256")
    )
    if not identity_valid or not isinstance(files, dict) or files != actual_files:
        return False
    if attestation.get("manifestDigest") != canonical_digest(files):
        return False
    if not _verify_signed_document(attestation):
        return False
    if not _authenticated_capture_valid(
        output, run_context, run_context.get("taskId"), anchor_path
    ):
        return False
    rebuilt = _rebuild_retained_summary(output, anchor_path)
    return (
        summary.get("passed") is True
        and isinstance(summary.get("checks"), dict)
        and all(value is True for value in summary["checks"].values())
        and rebuilt == summary
    )


def promote_candidate_anchor(output: Path, candidate_anchor: Path, trusted_anchor: Path) -> None:
    candidate_read = False
    try:
        with _held_evidence_root(output) as rooted:
            rooted_candidate = _path_from_held_root(rooted, candidate_anchor)
            candidate_bytes = _read_audited_regular_file(rooted_candidate)
            candidate_read = True
            with tempfile.TemporaryDirectory(prefix="tangying-anchor-audit-") as directory:
                audited_anchor = Path(directory) / "candidate-anchor.json"
                audited_anchor.write_bytes(candidate_bytes)
                if not validate_retained_pack(rooted, audited_anchor):
                    raise AssertionError("candidate pack failed full retained revalidation")
    except (OSError, ValueError) as error:
        message = (
            "candidate pack failed full retained revalidation"
            if candidate_read
            else "candidate anchor is missing"
        )
        raise AssertionError(message) from error
    trusted_anchor.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=trusted_anchor.parent, delete=False
    ) as temporary:
        temporary.write(candidate_bytes)
        temporary_path = Path(temporary.name)
    temporary_path.replace(trusted_anchor)


def _open_directory_tree_no_symlinks(path: Path, *, trusted_root: Path, create: bool = True) -> int:
    """Open/create ``path`` without following links below a fixed trusted root."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise SystemExit("platform cannot safely reject candidate path symlinks")
    try:
        relative = path.relative_to(trusted_root)
    except ValueError as error:
        raise SystemExit(f"candidate path must be inside {trusted_root}") from error
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current_fd = os.open(trusted_root, flags)
    try:
        for component in relative.parts:
            try:
                metadata = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise SystemExit(f"candidate path changed or disappeared: {component}")
                try:
                    os.mkdir(component, dir_fd=current_fd)
                except FileExistsError:
                    pass
                metadata = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise SystemExit(f"candidate path contains a symlink component: {component}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise SystemExit(f"candidate path contains a non-directory component: {component}")
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as error:
                raise SystemExit(
                    f"candidate path contains a symlink or unsafe component: {component}"
                ) from error
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


class CandidateWorkspace:
    """Private candidate staging plus held identities for fail-closed publication."""

    def __init__(
        self,
        *,
        requested: Path,
        staging_path: Path,
        staging_name: str,
        trusted_root: Path,
        requested_parent_relative: Path,
        parent_fd: int,
        staging_parent_fd: int,
        requested_fd: int,
        staging_fd: int,
    ) -> None:
        self.requested = requested
        self._staging_path = staging_path
        self._staging_name = staging_name
        self.output = FDRootedDirectory(staging_fd, staging_path)
        self._trusted_root = trusted_root
        self._requested_parent_relative = requested_parent_relative
        self._parent_fd = parent_fd
        self._staging_parent_fd = staging_parent_fd
        self._requested_fd = requested_fd
        self._staging_fd = staging_fd
        self._requested_identity = self._identity(requested_fd)
        self._staging_identity = self._identity(staging_fd)
        self._published = False
        self._closed = False

    @staticmethod
    def _identity(directory_fd: int) -> tuple[int, int]:
        metadata = os.fstat(directory_fd)
        return metadata.st_dev, metadata.st_ino

    def _entry_identity(self, name: str, parent_fd: int) -> tuple[int, int] | None:
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(metadata.st_mode):
            return None
        return metadata.st_dev, metadata.st_ino

    def _entry_name_for_identity(self, parent_fd: int, identity: tuple[int, int]) -> str | None:
        for name in os.listdir(parent_fd):
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                stat.S_ISDIR(metadata.st_mode)
                and (
                    metadata.st_dev,
                    metadata.st_ino,
                )
                == identity
            ):
                return name
        return None

    def assert_integrity(self) -> None:
        if self._closed:
            raise SystemExit("candidate workspace is closed")
        verification_fd = _open_directory_tree_no_symlinks(
            self._trusted_root / self._requested_parent_relative,
            trusted_root=self._trusted_root,
            create=False,
        )
        try:
            if self._identity(verification_fd) != self._identity(self._parent_fd):
                raise SystemExit("candidate parent changed during the candidate run")
        finally:
            os.close(verification_fd)
        if self._entry_identity(self.requested.name, self._parent_fd) != self._requested_identity:
            raise SystemExit("candidate output was replaced during the candidate run")
        if (
            self._entry_identity(self._staging_name, self._staging_parent_fd)
            != self._staging_identity
        ):
            raise SystemExit("private candidate staging changed during the candidate run")

    def publish(self) -> FDRootedDirectory:
        """Atomically replace the still-empty candidate entry with private staging."""
        self.assert_integrity()
        try:
            os.rmdir(self.requested.name, dir_fd=self._parent_fd)
        except OSError as error:
            raise SystemExit("candidate output changed before publication") from error
        try:
            os.rename(
                self._staging_name,
                self.requested.name,
                src_dir_fd=self._staging_parent_fd,
                dst_dir_fd=self._parent_fd,
            )
        except OSError as error:
            raise SystemExit("candidate output changed during publication") from error
        if self._entry_identity(self.requested.name, self._parent_fd) != self._staging_identity:
            raise SystemExit("candidate output changed after publication")
        self._published = True
        self.output.display_path = self.requested
        return self.output

    def cleanup(self) -> None:
        if self._closed:
            return
        try:
            if not self._published:
                self.output.clear()
                staging_entry = self._entry_name_for_identity(
                    self._staging_parent_fd, self._staging_identity
                )
                if staging_entry is not None:
                    os.rmdir(staging_entry, dir_fd=self._staging_parent_fd)
                if (
                    self._entry_identity(self.requested.name, self._parent_fd)
                    == self._requested_identity
                ):
                    shutil.rmtree(self.requested.name, dir_fd=self._parent_fd)
        finally:
            for directory_fd in (
                self._staging_fd,
                self._requested_fd,
                self._parent_fd,
                self._staging_parent_fd,
            ):
                try:
                    os.close(directory_fd)
                except OSError:
                    pass
            self._closed = True


def _prepare_candidate_output(requested: Path) -> CandidateWorkspace:
    configured_root = Path(os.path.abspath(os.fspath(CANDIDATE_ROOT)))
    configured_root.mkdir(parents=True, exist_ok=True)
    try:
        root = configured_root.resolve(strict=True)
    except OSError as error:
        raise SystemExit("trusted candidate root is unavailable") from error
    if not root.is_dir():
        raise SystemExit("trusted candidate root is not a directory")
    requested_lexical = Path(os.path.abspath(os.fspath(requested)))
    try:
        relative = requested_lexical.relative_to(configured_root)
    except ValueError as error:
        raise SystemExit(f"candidate output must be inside {configured_root}") from error
    output = root / relative
    retained_packs = tuple(root / name for name in RETAINED_PACK_NAMES)
    if not relative.parts or any(output == pack or pack in output.parents for pack in retained_packs):
        raise SystemExit("candidate output cannot be the retained root or a retained evidence pack")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise SystemExit("platform cannot safely clear candidate output")

    parent_fd = _open_directory_tree_no_symlinks(output.parent, trusted_root=root)
    try:
        staging_parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except BaseException:
        os.close(parent_fd)
        raise
    requested_fd = None
    staging_fd = None
    staging_name = None
    try:
        try:
            metadata = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if stat.S_ISLNK(metadata.st_mode):
                raise SystemExit("candidate output cannot be a symlink")
            if not stat.S_ISDIR(metadata.st_mode):
                raise SystemExit("candidate output exists but is not a directory")
            try:
                shutil.rmtree(output.name, dir_fd=parent_fd)
            except OSError as error:
                raise SystemExit("candidate output changed during symlink-safe cleanup") from error
        try:
            os.mkdir(output.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as error:
            raise SystemExit("candidate output changed during symlink-safe preparation") from error
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            requested_fd = os.open(output.name, flags, dir_fd=parent_fd)
        except OSError as error:
            raise SystemExit("candidate output changed during preparation") from error
        for _attempt in range(16):
            staging_name = f".{output.name}.staging-{secrets.token_hex(16)}"
            try:
                os.mkdir(staging_name, mode=0o700, dir_fd=staging_parent_fd)
                break
            except FileExistsError:
                continue
        else:
            raise SystemExit("could not allocate private candidate staging")
        try:
            staging_fd = os.open(staging_name, flags, dir_fd=staging_parent_fd)
        except OSError as error:
            raise SystemExit("private candidate staging changed during preparation") from error
        verification_fd = _open_directory_tree_no_symlinks(
            output.parent, trusted_root=root, create=False
        )
        try:
            if CandidateWorkspace._identity(parent_fd) != CandidateWorkspace._identity(
                verification_fd
            ):
                raise SystemExit("candidate parent changed during symlink-safe preparation")
        finally:
            os.close(verification_fd)
        workspace = CandidateWorkspace(
            requested=output,
            staging_path=root / staging_name,
            staging_name=staging_name,
            trusted_root=root,
            requested_parent_relative=output.parent.relative_to(root),
            parent_fd=parent_fd,
            staging_parent_fd=staging_parent_fd,
            requested_fd=requested_fd,
            staging_fd=staging_fd,
        )
        workspace.assert_integrity()
        return workspace
    except BaseException:
        if staging_name is not None:
            try:
                metadata = os.stat(staging_name, dir_fd=staging_parent_fd, follow_symlinks=False)
                held = os.fstat(staging_fd) if staging_fd is not None else None
                if (
                    held is not None
                    and stat.S_ISDIR(metadata.st_mode)
                    and (metadata.st_dev, metadata.st_ino) == (held.st_dev, held.st_ino)
                ):
                    shutil.rmtree(staging_name, dir_fd=staging_parent_fd)
            except OSError:
                pass
        try:
            metadata = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
            held = os.fstat(requested_fd) if requested_fd is not None else None
            if (
                held is not None
                and stat.S_ISDIR(metadata.st_mode)
                and (metadata.st_dev, metadata.st_ino) == (held.st_dev, held.st_ino)
            ):
                shutil.rmtree(output.name, dir_fd=parent_fd)
        except OSError:
            pass
        if staging_fd is not None:
            os.close(staging_fd)
        if requested_fd is not None:
            os.close(requested_fd)
        os.close(parent_fd)
        os.close(staging_parent_fd)
        raise


def _wait_for_browser_evidence(output: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (output / "browser-evidence.json").exists():
            return
        time.sleep(0.1)


def _run_candidate(args: argparse.Namespace) -> int:
    workspace = _prepare_candidate_output(args.output)
    try:
        output = workspace.output
        ports = tuple(int(value) for value in args.ports.split(",") if value)
        if ports and len(ports) != 4:
            raise SystemExit("--ports requires fleet,gateway,runtime1,runtime2")
        run_id = uuid.uuid4().hex
        episode_nonce = secrets.token_hex(32)
        started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        receiver = AuthenticatedCaptureReceiver(
            output,
            run_id=run_id,
            episode_nonce=episode_nonce,
            integrity_check=workspace.assert_integrity,
        )
        try:
            receiver.start()
        except BaseException:
            receiver.stop()
            raise
        candidate_valid = False
        with tempfile.TemporaryDirectory(prefix="tangying-robocasa-e2e-") as directory:
            try:
                stack = start_robocasa_handoff_stack(
                    Path(directory),
                    human_speed=args.human_speed,
                    ports=ports or None,
                    episode_nonce=episode_nonce,
                )
            except BaseException:
                receiver.stop()
                raise
            try:
                public_base_url = args.public_base_url.rstrip("/") or stack.base_url
                initial = stack.api("/v1/world")
                if initial.get("acceptanceNonce") != episode_nonce:
                    raise AssertionError("server did not expose the runner episode nonce")
                trajectory_samples = [initial]
                trajectory_errors: list[Exception] = []
                trajectory_stop = threading.Event()

                def sample_world() -> None:
                    while not trajectory_stop.wait(0.01):
                        try:
                            snapshot = stack.api("/v1/world")
                            if snapshot.get("acceptanceNonce") != episode_nonce:
                                raise AssertionError(
                                    "world nonce changed during acceptance episode"
                                )
                            if snapshot.get("revision", -1) > trajectory_samples[-1].get(
                                "revision", -1
                            ):
                                trajectory_samples.append(snapshot)
                            elif snapshot.get("revision") == trajectory_samples[-1].get("revision"):
                                trajectory_samples[-1] = snapshot
                        except (AssertionError, OSError, TypeError, ValueError) as error:
                            # Network, decoding, and schema errors are surfaced on the
                            # controlling thread after the sampler has stopped.
                            trajectory_errors.append(error)
                            trajectory_stop.set()

                sampler = threading.Thread(
                    target=sample_world,
                    name="robocasa-world-evidence",
                    daemon=True,
                )
                sampler.start()
                initial_joints = _canonical_joints(initial)
                try:
                    task_id = stack.create_and_approve(HANDOFF_PROMPT)
                    running_experience = stack.wait_experience_step(
                        task_id, step_index=0, status="RUNNING", timeout=30
                    )
                    proposal = stack.propose_update(
                        task_id,
                        int(running_experience["revision"]),
                        TASK_UPDATE_PROMPT,
                    )
                    proposed_revision = proposal["proposal"]["revision"]
                    confirmation = stack.confirm_update(
                        task_id,
                        int(proposed_revision["revision"]),
                        int(running_experience["revision"]),
                    )
                    waiting_experience = stack.experience(task_id)
                    if (
                        confirmation.get("revision", {}).get("status") != "WAITING_SAFE_POINT"
                        or waiting_experience.get("updateStatus") != "WAITING_SAFE_POINT"
                    ):
                        raise AssertionError(
                            "task update did not enter the physical safe-point gate"
                        )
                    moving = stack.wait_world(
                        lambda snapshot: (
                            snapshot.get("revision", 0) > initial["revision"]
                            and all(
                                any(
                                    abs(value - initial_joints[robot_id].get(key, value)) > 1e-3
                                    for key, value in _canonical_joints(snapshot)[robot_id].items()
                                )
                                for robot_id in REQUIRED_ROBOT_IDS
                            )
                        ),
                        timeout=120,
                    )
                    task = stack.wait_task(task_id)
                    final_experience = stack.wait_experience(
                        task_id,
                        lambda value: (
                            value.get("revision") == 2
                            and all(
                                step.get("status") == "SATISFIED" for step in value.get("steps", [])
                            )
                        ),
                        timeout=120,
                    )
                    revision_history = stack.revision_history(task_id)
                    world = stack.wait_world(
                        lambda snapshot: (
                            snapshot.get("entities", {})
                            .get("red-block", {})
                            .get("relations", {})
                            .get("inside")
                            == "right-target-zone"
                            and _source_freshness_valid(snapshot)
                        )
                    )
                finally:
                    trajectory_stop.set()
                    sampler.join(timeout=5)
                if trajectory_errors:
                    raise trajectory_errors[0]
                latest_sample = trajectory_samples[-1]
                if (
                    latest_sample.get("revision", -1) >= world.get("revision", -1)
                    and latest_sample.get("entities", {})
                    .get("red-block", {})
                    .get("relations", {})
                    .get("inside")
                    == "right-target-zone"
                    and _source_freshness_valid(latest_sample)
                ):
                    world = latest_sample
                if world.get("revision", -1) > trajectory_samples[-1].get("revision", -1):
                    trajectory_samples.append(world)
                elif world.get("revision") == trajectory_samples[-1].get("revision"):
                    trajectory_samples[-1] = world
                intent_document = stack.api(f"/v1/tasks/{task_id}/intents")
                intents = intent_document["intents"]
                workspace.assert_integrity()
                write_json(output / "world-initial.json", initial)
                write_json(output / "world-moving.json", moving)
                write_json(output / "world-final.json", world)
                write_json(
                    output / "world-trajectory.json",
                    {
                        "schemaVersion": "tangying.world-trajectory.v1",
                        "episodeNonce": episode_nonce,
                        "taskId": task_id,
                        "samples": trajectory_samples,
                    },
                )
                write_json(output / "task.json", task)
                write_json(
                    output / "task-update.json",
                    {
                        "schemaVersion": "tangying.robocasa-task-update.v1",
                        "taskId": task_id,
                        "originalRequest": HANDOFF_PROMPT,
                        "updateRequest": TASK_UPDATE_PROMPT,
                        "runningExperience": running_experience,
                        "proposal": proposal,
                        "confirmation": confirmation,
                        "waitingExperience": waiting_experience,
                        "finalExperience": final_experience,
                        "revisionHistory": revision_history,
                    },
                )
                write_json(output / "intents.json", intent_document)
                write_json(output / "events.json", stack.api(f"/v1/tasks/{task_id}/domain-events"))
                write_json(output / "devices.json", stack.api("/v1/devices"))
                write_json(output / "harness-verdicts.json", intents)
                run_context = {
                    "schemaVersion": "tangying.robocasa-acceptance-run.v3",
                    "runId": run_id,
                    "episodeNonce": episode_nonce,
                    "taskId": task_id,
                    "request": HANDOFF_PROMPT,
                    "taskUpdate": {
                        "request": TASK_UPDATE_PROMPT,
                        "revision": 2,
                        "safePointStatus": "WAITING_SAFE_POINT",
                    },
                    "adapter": "robocasa",
                    "sceneId": "robocasa-handoff-v1",
                    "publicBaseUrl": public_base_url,
                    "startedAt": started_at,
                    "snapshots": {
                        "initial": _snapshot_record(initial),
                        "moving": _snapshot_record(moving),
                        "final": _snapshot_record(world),
                    },
                }
                write_json(output / "run-context.json", run_context)
                workspace.assert_integrity()
                manifest, visual = collect_visual_evidence(
                    stack,
                    world,
                    output,
                    run_id=run_id,
                    task_id=task_id,
                    episode_nonce=episode_nonce,
                    public_base_url=(
                        public_base_url if public_base_url != stack.base_url else None
                    ),
                )
                workspace.assert_integrity()
                anchor_path = output / "capture-anchor-candidate.json"
                receiver.bind_task(task_id, anchor_path)
                print(f"browser capture session: {output / 'capture-session.json'}", flush=True)
                if not receiver.wait(args.browser_evidence_timeout):
                    raise SystemExit(
                        "browser evidence timeout; use the repository uploader before "
                        "the bounded wait expires"
                    )
                summary = build_acceptance_summary(
                    output=output,
                    run_context=run_context,
                    task_id=task_id,
                    task=task,
                    initial_world=initial,
                    moving_world=moving,
                    world=world,
                    manifest=manifest,
                    intents=intents,
                    visual=visual,
                    trusted_anchor_path=anchor_path,
                )
                receiver.finalize(summary, anchor_path)
                workspace.assert_integrity()
                if not summary["passed"]:
                    failed = sorted(
                        name for name, passed in summary["checks"].items() if passed is not True
                    )
                    print(
                        "candidate acceptance failed checks: " + ", ".join(failed),
                        file=sys.stderr,
                    )
                    raise SystemExit(1)
                if not validate_retained_pack(output, anchor_path):
                    print("candidate retained-pack self-validation failed", file=sys.stderr)
                    raise SystemExit(1)
                candidate_valid = True
            finally:
                stack.stop()
                receiver.stop()
        if not candidate_valid:
            raise SystemExit(1)
        workspace.publish()
        return 0
    finally:
        workspace.cleanup()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, promote, or revalidate authenticated RoboCasa acceptance evidence."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--candidate", action="store_true")
    mode.add_argument("--promote-anchor", action="store_true")
    mode.add_argument("--revalidate", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, default=TRUSTED_ANCHOR_PATH)
    parser.add_argument("--ports", default="")
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--browser-evidence-timeout", type=float, default=300.0)
    parser.add_argument("--human-speed", type=float, default=0.02)
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if args.candidate and not (
        math.isfinite(args.browser_evidence_timeout) and 0 < args.browser_evidence_timeout <= 600
    ):
        parser.error("--browser-evidence-timeout must be greater than zero and at most 600 seconds")
    output = (
        Path(os.path.abspath(os.fspath(args.output))) if args.candidate else args.output.resolve()
    )
    anchor = args.anchor.resolve()
    if args.revalidate:
        if not validate_retained_pack(output, anchor):
            print(f"retained RoboCasa acceptance failed revalidation: {output}", file=sys.stderr)
            return 1
        print(f"retained RoboCasa acceptance revalidated: {output}")
        return 0
    if args.promote_anchor:
        candidate_anchor = output / "capture-anchor-candidate.json"
        try:
            promote_candidate_anchor(output, candidate_anchor, anchor)
        except AssertionError as error:
            print(f"candidate promotion rejected: {error}", file=sys.stderr)
            return 1
        print(f"promoted RoboCasa acceptance anchor: {anchor}")
        return 0
    return _run_candidate(args)


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
