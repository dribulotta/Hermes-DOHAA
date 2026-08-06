"""Hermes adapter using its documented OpenAI-compatible API surface."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Sequence

from hermes_dohaa.contracts.models import TaskContract
from hermes_dohaa.runtime.base import Proposal, VerifierFeedback


_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


class HermesApiError(RuntimeError):
    pass


@dataclass(slots=True)
class HermesApiRuntime:
    base_url: str = "http://127.0.0.1:8642"
    api_key: str | None = None
    model: str = "hermes-agent"
    timeout_seconds: float = 300.0
    session_id: str | None = None
    session_key: str | None = None
    reasoning_effort: str | None = None

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
        request = urllib.request.Request(
            self._chat_completions_url(),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HermesApiError(f"Hermes API request failed: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise HermesApiError("Hermes API response did not contain choices[0].message.content") from exc
        return parse_proposal_content(content)

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
        raise HermesApiError("Hermes proposal content must be a string")
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
        raise HermesApiError("Hermes returned non-JSON proposal content") from exc
    if not isinstance(raw, dict):
        raise HermesApiError("Hermes proposal root must be an object")
    try:
        return Proposal.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise HermesApiError(f"Hermes proposal schema is invalid: {exc}") from exc


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
