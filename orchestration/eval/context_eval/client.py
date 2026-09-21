"""真实兼容 API 调用与内容寻址缓存；不记录鉴权信息。"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .dataset import canonical


class Client:
    def __init__(self, root, config=None, enabled=True):
        self._locks = {}
        self._guard = threading.Lock()
        self.root = Path(root)
        self.cache = self.root / "responses"
        self.cache.mkdir(parents=True, exist_ok=True)
        saved = self.root / "model-config.json"
        settings = json.loads(saved.read_text()) if saved.exists() else {}
        private = {}
        if config:
            for line in Path(config).read_text().splitlines():
                k, sep, v = line.partition("=")
                if sep and k.strip() in {"AGENT_BASE_URL", "AGENT_MODEL", "AGENT_API_KEY"}:
                    private[k.strip()] = v.strip().strip("\"'")
        self.url = os.environ.get(
            "AGENT_BASE_URL", private.get("AGENT_BASE_URL", settings.get("base_url", ""))
        ).rstrip("/")
        self.model = os.environ.get(
            "AGENT_MODEL", private.get("AGENT_MODEL", settings.get("model", ""))
        )
        self.key = os.environ.get("AGENT_API_KEY", private.get("AGENT_API_KEY", ""))
        self.enabled = enabled
        if enabled and not (self.url and self.model):
            raise ValueError("需要真实模型配置，不能用规则冒充模型")
        if enabled and settings and settings != {"base_url": self.url, "model": self.model}:
            raise ValueError("模型配置变化，必须使用新的实验目录")
        if enabled:
            saved.write_text(canonical({"base_url": self.url, "model": self.model}) + "\n")

    def call(self, messages, seed):
        request = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "seed": seed,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        if urlsplit(self.url).hostname == "api.deepseek.com":
            request["thinking"] = {"type": "disabled"}
        identity = hashlib.sha256(
            canonical({"endpoint": self.url, "request": request}).encode()
        ).hexdigest()
        with self._guard:
            lock = self._locks.setdefault(identity, threading.Lock())
        with lock:
            return self._request(request, identity)

    def _request(self, request, identity):
        path = self.cache / (identity + ".json")
        if path.exists():
            return json.loads(path.read_text())
        if not self.enabled:
            raise FileNotFoundError(f"响应缓存缺失：{identity}")
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        errors = []
        start = time.perf_counter()
        raw = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(
                        self.url + "/chat/completions", canonical(request).encode(), headers
                    ),
                    timeout=60,
                ) as response:
                    chunks = []
                    deadline = time.monotonic() + 90
                    while True:
                        block = response.read1(16384)
                        if not block:
                            break
                        chunks.append(block)
                        if time.monotonic() > deadline:
                            raise TimeoutError("total response deadline")
                    raw = json.loads(b"".join(chunks))
                break
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
            ) as error:
                errors.append(type(error).__name__)
                if attempt < 2:
                    time.sleep(2**attempt)
        latency = time.perf_counter() - start
        protocol_error = None
        answer = None
        if raw is not None:
            try:
                answer = json.loads(raw["choices"][0]["message"]["content"])
            except (KeyError, IndexError, ValueError, TypeError) as error:
                protocol_error = type(error).__name__
        else:
            protocol_error = "transport_failure"
        record = {
            "request_hash": identity,
            "request": request,
            "response": raw,
            "answer": answer,
            "latency_s": latency,
            "usage": raw.get("usage") if raw else None,
            "response_model": raw.get("model") if raw else None,
            "protocol_error": protocol_error,
            "transport_errors": errors,
            "response_sha256": hashlib.sha256(canonical(raw).encode()).hexdigest(),
        }
        with tempfile.NamedTemporaryFile(
            mode="w", dir=self.cache, suffix=".tmp", delete=False
        ) as stream:
            stream.write(canonical(record) + "\n")
            tmp = Path(stream.name)
        tmp.replace(path)
        return record
