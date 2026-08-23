from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from pydantic import ValidationError

from .contracts import InferenceRequest
from .providers import PolicyProvider


class PolicyHTTPServer(ThreadingHTTPServer):
    provider: PolicyProvider
    max_request_bytes: int


def handler_for(provider: PolicyProvider, max_request_bytes: int = 1 << 20):
    del provider, max_request_bytes

    class Handler(BaseHTTPRequestHandler):
        server: PolicyHTTPServer

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            if self.path == "/v1/manifest":
                self._json(HTTPStatus.OK, self.server.provider.manifest.model_dump(by_alias=True))
                return
            self._json(HTTPStatus.NOT_FOUND, {"code": "NOT_FOUND"})

        def do_POST(self) -> None:
            if self.path != "/v1/infer":
                self._json(HTTPStatus.NOT_FOUND, {"code": "NOT_FOUND"})
                return
            length = self._content_length()
            if length < 0:
                self._json(HTTPStatus.BAD_REQUEST, {"code": "CONTENT_LENGTH_REQUIRED"})
                return
            if length > self.server.max_request_bytes:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"code": "REQUEST_TOO_LARGE"})
                return
            body = self.rfile.read(length)
            try:
                request = InferenceRequest.model_validate_json(body)
                result = self.server.provider.infer(request)
            except (ValidationError, ValueError):
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"code": "POLICY_REQUEST_REJECTED"})
                return
            except Exception:  # noqa: BLE001 - provider internals must never leak over HTTP
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"code": "POLICY_PROVIDER_FAILED"})
                return
            self._json(HTTPStatus.OK, result.model_dump(by_alias=True))

        def _content_length(self) -> int:
            try:
                return int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                return -1

        def _json(self, status: HTTPStatus, document: dict[str, Any]) -> None:
            wire = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(wire)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(wire)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return Handler


def create_server(
    provider: PolicyProvider,
    *,
    host: str = "127.0.0.1",
    port: int = 8091,
    max_request_bytes: int = 1 << 20,
) -> PolicyHTTPServer:
    server = PolicyHTTPServer((host, port), handler_for(provider, max_request_bytes))
    server.provider = provider
    server.max_request_bytes = max_request_bytes
    return server


def serve_in_thread(
    provider: PolicyProvider,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    max_request_bytes: int = 1 << 20,
) -> tuple[PolicyHTTPServer, Thread]:
    server = create_server(provider, host=host, port=port, max_request_bytes=max_request_bytes)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
