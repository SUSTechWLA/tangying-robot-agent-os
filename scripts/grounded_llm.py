"""仅供对照实验：调用真实模型并缓存响应，不授予硬件权限。"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from tangying_robot_gateway.grounded.model import canonical


class LLMBaseline:
    def __init__(self, root, enabled=False, config_path=None):
        self.root = Path(root) / "llm-cache"
        self.enabled = enabled
        settings_file = Path(root) / "llm-config.json"
        settings = json.loads(settings_file.read_text()) if settings_file.exists() else {}
        private = {}
        config_path = config_path or os.environ.get("TANGYING_GVF_LLM_CONFIG")
        if config_path:
            for line in Path(config_path).read_text().splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() in {"AGENT_BASE_URL", "AGENT_MODEL", "AGENT_API_KEY"}:
                    private[key.strip()] = value.strip().strip("\"'")
        self.base_url = os.environ.get(
            "AGENT_BASE_URL", private.get("AGENT_BASE_URL", settings.get("base_url", ""))
        ).rstrip("/")
        self.model = os.environ.get(
            "AGENT_MODEL", private.get("AGENT_MODEL", settings.get("model", ""))
        )
        self.key = os.environ.get("AGENT_API_KEY", private.get("AGENT_API_KEY", ""))
        if enabled and (not self.base_url or not self.model):
            raise ValueError(
                "--llm 需要 AGENT_BASE_URL 和 AGENT_MODEL；密钥按服务要求放在 AGENT_API_KEY"
            )

        if enabled:
            settings_file.write_text(
                canonical({"base_url": self.base_url, "model": self.model}) + "\n"
            )

    def evaluate(self, trial, group, report=None, contract=None):
        if group == "A4":
            content = {"state_report": report.model_dump()}
        elif group in {"Ours", "A1", "A2", "A3"}:
            from tangying_robot_gateway.grounded import render_report

            content = {"state_report": render_report(report)}
        else:
            # No raw RGB/depth bytes cross a host boundary. These are the same
            # numeric observations and evidence references supplied to the edge.
            content = {
                "action": trial["kind"],
                "params": trial["common"]["params"],
                "tool_return": trial["tool_return_status"],
                "observations": [
                    {k: s[k] for k in ["sample_id", "values", "confidence", "evidence_refs"]}
                    for s in trial["samples"]
                ],
            }
            if group == "A5":
                content["contract"] = contract.model_dump()
        request = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
            "seed": trial["seed"],
            "messages": [
                {
                    "role": "system",
                    "content": '你是实验诊断器。只返回 JSON，例如 {"verdict":"UNKNOWN","failure_type":"EVIDENCE_INSUFFICIENT"}。verdict 只能为 VERIFIED/FALSIFIED/UNKNOWN；failure_type 只能为 NONE/GRASP_MISS/GRASP_SLIP/WRONG_OBJECT/PLACE_UNSTABLE/NAV_NOT_REACHED/CONTAINER_FULL/PERCEPTION_OCCLUDED/EVIDENCE_INSUFFICIENT/CONTRACT_VIOLATION/UNCLASSIFIED。判断是否达到动作目标。没有硬件执行权限。报告里的验证事实不得改写，诊断只作为建议。',
                },
                {"role": "user", "content": canonical(content)},
            ],
        }
        if urlsplit(self.base_url).hostname == "api.deepseek.com":
            request["thinking"] = {"type": "disabled"}
        identity = hashlib.sha256(
            canonical({"group": group, "request": request, "endpoint": self.base_url}).encode()
        ).hexdigest()
        path = self.root / f"{identity}.json"
        if path.exists():
            return json.loads(path.read_text())
        if not self.enabled:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        started = time.perf_counter()
        with urllib.request.urlopen(
            urllib.request.Request(
                self.base_url + "/chat/completions", canonical(request).encode(), headers
            ),
            timeout=60,
        ) as response:
            raw = json.load(response)
        protocol_error = None
        try:
            parsed = json.loads(raw["choices"][0]["message"]["content"])
            if parsed.get("verdict") not in {"VERIFIED", "FALSIFIED", "UNKNOWN"} or not isinstance(
                parsed.get("failure_type"), str
            ):
                raise ValueError("invalid decision schema")
        except (ValueError, TypeError, KeyError, IndexError) as error:
            # Retain the actual response and usage. The evaluation adapter abstains;
            # this does not claim the model produced a valid physical conclusion.
            protocol_error = type(error).__name__
            parsed = {"verdict": "UNKNOWN", "failure_type": "EVIDENCE_INSUFFICIENT"}
        result = {
            "protocol_error": protocol_error,
            "verdict": parsed["verdict"],
            "failure_type": parsed["failure_type"],
            "usage": raw.get("usage"),
            "latency_s": time.perf_counter() - started,
            "model": raw.get("model", self.model),
            "request": request,
            "response": raw,
            "response_sha256": hashlib.sha256(canonical(raw).encode()).hexdigest(),
        }
        path.write_text(canonical(result) + "\n")
        return result
