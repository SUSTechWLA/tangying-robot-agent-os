"""Upload one browser-produced RoboCasa capture to the active loopback receiver."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class UploadError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _load_private_session(path: Path) -> dict:
    try:
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if not stat.S_ISREG(metadata.st_mode) or mode & 0o077:
            raise UploadError("capture session must be a regular file with mode 0600")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise UploadError("capture session must be owned by the current user")
        value = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UploadError("capture session is unreadable") from error
    if not isinstance(value, dict) or value.get("schemaVersion") != "tangying.capture-session.v1":
        raise UploadError("capture session schema is invalid")
    receiver_url = value.get("receiverUrl")
    parsed = urlsplit(receiver_url if isinstance(receiver_url, str) else "")
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.path != "/v1/capture"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UploadError("capture receiver must be the repository loopback endpoint")
    if not isinstance(value.get("bearerSecret"), str) or not value["bearerSecret"]:
        raise UploadError("capture session has no bearer credential")
    return value


def upload_capture(session_path: Path, payload_path: Path, *, timeout: float = 30) -> int:
    if not 0 < timeout <= 120:
        raise UploadError("upload timeout must be greater than zero and at most 120 seconds")
    session = _load_private_session(session_path)
    try:
        payload_bytes = payload_path.read_bytes()
        payload = json.loads(payload_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UploadError("browser payload is unreadable") from error
    if not isinstance(payload, dict) or not payload_bytes or len(payload_bytes) > 80 * 1024 * 1024:
        raise UploadError("browser payload size or schema is invalid")
    if any(
        payload.get(name) != session.get(name)
        for name in ("runId", "episodeNonce", "taskId")
    ):
        raise UploadError("browser payload does not match the active capture session")
    request = Request(session["receiverUrl"], data=payload_bytes, method="POST")
    request.add_header("Authorization", f"Bearer {session['bearerSecret']}")
    request.add_header("Content-Type", "application/json")
    request.add_header("Content-Length", str(len(payload_bytes)))
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 201:
                raise UploadError(f"capture receiver returned HTTP {response.status}")
    except HTTPError as error:
        raise UploadError(f"capture receiver rejected upload with HTTP {error.code}") from error
    except OSError as error:
        raise UploadError("capture receiver is unavailable") from error
    print("browser capture accepted (HTTP 201)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        return upload_capture(args.session, args.payload, timeout=args.timeout)
    except UploadError as error:
        print(f"browser capture upload failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
