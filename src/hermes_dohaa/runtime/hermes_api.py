"""Hermes adapter using its documented OpenAI-compatible API surface."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Sequence

from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Proposal, VerifierFeedback
from hermes_dohaa.runtime.usage import (
    normalize_response_usage,
    unavailable_usage,
)


_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


class HermesApiError(RuntimeError):
    """A fail-closed adapter failure with safe, stable diagnostics."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = _json_clone(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": _json_clone(self.details),
        }


@dataclass(slots=True)
class HermesApiRuntime:
    base_url: str = "http://127.0.0.1:8642"
    api_key: str | None = None
    model: str = "hermes-agent"
    timeout_seconds: float = 300.0
    session_id: str | None = None
    session_key: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    sampling_seed: int | None = None
    usage_records: list[dict[str, Any]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _usage_call_index: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.reasoning_effort is not None:
            if not isinstance(self.reasoning_effort, str):
                raise ValueError("reasoning_effort must be a string or None")
            normalized = self.reasoning_effort.strip().lower()
            if normalized not in _REASONING_EFFORTS:
                allowed = ", ".join(sorted(_REASONING_EFFORTS))
                raise ValueError(f"reasoning_effort must be one of: {allowed}")
            self.reasoning_effort = normalized
        if self.temperature is not None:
            if (
                isinstance(self.temperature, bool)
                or not isinstance(self.temperature, (int, float))
                or not 0 <= self.temperature <= 2
            ):
                raise ValueError("temperature must be between 0 and 2")
            self.temperature = float(self.temperature)
        if self.top_p is not None:
            if (
                isinstance(self.top_p, bool)
                or not isinstance(self.top_p, (int, float))
                or not 0 < self.top_p <= 1
            ):
                raise ValueError("top_p must be greater than 0 and at most 1")
            self.top_p = float(self.top_p)
        if self.sampling_seed is not None:
            if isinstance(self.sampling_seed, bool) or not isinstance(
                self.sampling_seed,
                int,
            ):
                raise ValueError("sampling_seed must be an integer or None")

    def propose(
        self,
        contract: TaskContract,
        feedback: Sequence[VerifierFeedback | str],
    ) -> Proposal:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task_contract": contract.to_dict(),
                            "verifier_feedback": [
                                item.to_dict()
                                if isinstance(item, VerifierFeedback)
                                else item
                                for item in feedback
                            ],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
        }
        if self.reasoning_effort is not None:
            body["model_options"] = {
                "reasoning": {
                    "enabled": self.reasoning_effort != "none",
                    "effort": self.reasoning_effort,
                }
            }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.sampling_seed is not None:
            body["seed"] = self.sampling_seed
        request = urllib.request.Request(
            self._chat_completions_url(),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_bytes = response.read()
                content_type = _content_type(getattr(response, "headers", None))
                status = getattr(response, "status", None)
        except urllib.error.HTTPError as exc:
            self._record_usage(unavailable_usage("response.http_error"))
            raise HermesApiError(
                "response.http_error",
                "Hermes returned an unsuccessful HTTP response",
                _response_details(
                    stage="http",
                    status=exc.code,
                    content_type=_content_type(exc.headers),
                ),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            self._record_usage(unavailable_usage("response.timeout"))
            raise HermesApiError(
                "response.timeout", "Hermes API request timed out", {"stage": "request"}
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                self._record_usage(unavailable_usage("response.timeout"))
                raise HermesApiError(
                    "response.timeout",
                    "Hermes API request timed out",
                    {"stage": "request"},
                ) from exc
            self._record_usage(unavailable_usage("response.connection_failed"))
            raise HermesApiError(
                "response.connection_failed",
                "Hermes API connection failed",
                {"stage": "request"},
            ) from exc

        response_details = _response_details(
            stage="response",
            status=status,
            content_type=content_type,
            content=response_bytes,
        )
        try:
            payload = json.loads(response_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._record_usage(unavailable_usage("response.json_invalid"))
            raise HermesApiError(
                "response.json_invalid",
                "Hermes returned a non-JSON HTTP response",
                response_details,
            ) from exc

        usage = payload.get("usage") if isinstance(payload, dict) else None
        self._record_usage(normalize_response_usage(usage))

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise HermesApiError(
                "response.shape_invalid",
                "Hermes API response did not contain choices[0].message.content",
                response_details,
            ) from exc
        return parse_proposal_content(content)

    def _record_usage(self, record: dict[str, Any]) -> None:
        self._usage_call_index += 1
        observation = dict(record)
        observation["call_index"] = self._usage_call_index
        self.usage_records.append(observation)

    def _chat_completions_url(self) -> str:
        """Build the endpoint while accepting base URLs with or without /v1."""
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = self.api_key or os.getenv("HERMES_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if self.session_id:
            headers["X-Hermes-Session-Id"] = self.session_id
        if self.session_key:
            headers["X-Hermes-Session-Key"] = self.session_key
        return headers


def parse_proposal_content(content: Any) -> Proposal:
    if not isinstance(content, str):
        raise HermesApiError(
            "response.shape_invalid",
            "Hermes proposal content must be a string",
            {"stage": "proposal_content", "classification": "unknown"},
        )
    details = _content_details(content)
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise HermesApiError(
            "proposal.content_non_json",
            "Hermes returned non-JSON proposal content",
            details,
        ) from exc
    if not isinstance(raw, dict):
        raise HermesApiError(
            "proposal.schema_invalid", "Hermes proposal root must be an object", details
        )
    try:
        return Proposal.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise HermesApiError(
            "proposal.schema_invalid", "Hermes proposal schema is invalid", details
        ) from exc


def _content_type(headers: Any) -> str | None:
    value = headers.get("Content-Type") if headers is not None else None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.split(";", 1)[0].strip().lower()


def _response_details(
    *,
    stage: str,
    status: Any = None,
    content_type: str | None = None,
    content: bytes | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {"stage": stage}
    if isinstance(status, int):
        details["http_status"] = status
    if content_type is not None:
        details["content_type"] = content_type
    if content is not None:
        details.update(
            {
                "byte_length": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "classification": _classify_text(
                    content.decode("utf-8", errors="replace")
                ),
                "has_markdown_fence": b"```" in content,
            }
        )
    return details


def _content_details(content: str) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    return {
        "stage": "proposal_content",
        "character_length": len(content),
        "byte_length": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "classification": _classify_text(content),
        "has_markdown_fence": "```" in content,
    }


def _classify_text(content: str) -> str:
    stripped = content.strip()
    if not stripped:
        return "empty"
    if stripped.startswith("```"):
        return "fenced_json"
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return "text"
    return "json"


def _json_clone(value: Any) -> Any:
    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    )


_SYSTEM_PROMPT = """You are the cognitive runtime inside a DOHAA-controlled process.
Return exactly one JSON object and no prose or Markdown. Do not claim that work was
verified merely because you performed it. Treat every requested action as a proposal;
the external controller owns authorization and execution.
Preserve JSON types exactly. In particular, when an expected result is a JSON object,
return it as an object rather than as a string containing serialized JSON.

Required shape:
{
  "result": <JSON value>,
  "claims": [{"statement": "...", "evidence_ids": ["..."]}],
  "evidence": [
    {"evidence_id": "...", "kind": "...", "source": "...", "content": <JSON value>}
  ],
  "requested_actions": ["action.name"]
}
"""
