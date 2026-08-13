"""Safe, deterministic token-usage telemetry for runtime calls."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


REPORTED = "reported"
MISSING = "missing"
INVALID = "invalid"
UNAVAILABLE = "unavailable"

_CHAT_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
_RESPONSES_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
_FAILURE_STATUSES = (MISSING, INVALID, UNAVAILABLE)


def normalize_response_usage(value: Any) -> dict[str, Any]:
    """Normalize documented OpenAI-compatible usage without imputing tokens."""
    if value is None:
        return _failure(MISSING, "usage.missing", "response.usage was absent")
    if not isinstance(value, Mapping):
        return _failure(
            INVALID,
            "usage.invalid",
            "response.usage was not an object",
            {"classification": "non_object"},
        )

    keys = {key for key in value if isinstance(key, str)}
    has_chat = bool(keys & set(_CHAT_FIELDS[:-1]))
    has_responses = bool(keys & set(_RESPONSES_FIELDS[:-1]))
    if has_chat and has_responses:
        return _failure(
            INVALID,
            "usage.invalid",
            "response.usage mixed incompatible token field families",
            {"classification": "mixed_field_families"},
        )
    if has_responses:
        fields = _RESPONSES_FIELDS
        shape = "openai_responses"
    else:
        fields = _CHAT_FIELDS
        shape = "openai_chat_completions"

    missing = sorted(set(fields) - keys)
    if missing:
        return _failure(
            INVALID,
            "usage.invalid",
            "response.usage omitted required token fields",
            {
                "classification": "required_fields_missing",
                "missing_fields": missing,
            },
        )

    invalid = sorted(field for field in fields if not _is_token_count(value[field]))
    if invalid:
        return _failure(
            INVALID,
            "usage.invalid",
            "response.usage contained invalid token counts",
            {
                "classification": "invalid_token_counts",
                "invalid_fields": invalid,
            },
        )

    prompt = int(value[fields[0]])
    completion = int(value[fields[1]])
    total = int(value[fields[2]])
    if total <= 0 or total != prompt + completion:
        return _failure(
            INVALID,
            "usage.invalid",
            "response.usage token totals were inconsistent",
            {"classification": "inconsistent_total"},
        )
    return {
        "status": REPORTED,
        "source": "response.usage",
        "shape": shape,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def unavailable_usage(failure_code: str) -> dict[str, Any]:
    """Record why a runtime call could not expose response usage."""
    return _failure(
        UNAVAILABLE,
        "usage.unavailable",
        "token usage was unavailable because the runtime call failed",
        {"runtime_failure_code": failure_code},
    )


def summarize_usage(
    records: Iterable[Mapping[str, Any]],
    expected_calls: int,
) -> dict[str, Any]:
    """Summarize call-level telemetry while accepting legacy total-only records."""
    if isinstance(expected_calls, bool) or not isinstance(expected_calls, int):
        raise ValueError("expected_calls must be an integer")
    if expected_calls < 0:
        raise ValueError("expected_calls must not be negative")

    items = [dict(record) for record in records if isinstance(record, Mapping)]
    counts = {REPORTED: 0, MISSING: 0, INVALID: 0, UNAVAILABLE: 0}
    total_tokens = 0
    for record in items:
        total = reported_total_tokens(record)
        if total is not None:
            counts[REPORTED] += 1
            total_tokens += total
            continue
        status = record.get("status")
        counts[status if status in _FAILURE_STATUSES else INVALID] += 1

    unobserved = max(expected_calls - len(items), 0)
    unexpected = max(len(items) - expected_calls, 0)
    complete = (
        counts[REPORTED] == expected_calls
        and not any(counts[status] for status in _FAILURE_STATUSES)
        and unobserved == 0
        and unexpected == 0
    )
    return {
        "expected_calls": expected_calls,
        "observed_calls": len(items),
        "reported_calls": counts[REPORTED],
        "missing_calls": counts[MISSING],
        "invalid_calls": counts[INVALID],
        "unavailable_calls": counts[UNAVAILABLE],
        "unobserved_calls": unobserved,
        "unexpected_observations": unexpected,
        "reported_total_tokens": total_tokens,
        "complete": complete,
    }


def reported_total_tokens(record: Mapping[str, Any]) -> int | None:
    """Return a trustworthy total from normalized or legacy telemetry."""
    total = record.get("total_tokens")
    if not _is_token_count(total) or int(total) <= 0:
        return None
    status = record.get("status")
    if status is None:
        return int(total)
    if status != REPORTED:
        return None
    prompt = record.get("prompt_tokens")
    completion = record.get("completion_tokens")
    if not _is_token_count(prompt) or not _is_token_count(completion):
        return None
    if int(total) != int(prompt) + int(completion):
        return None
    return int(total)


def _failure(
    status: str,
    reason_code: str,
    reason: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "source": "response.usage",
        "reason_code": reason_code,
        "reason": reason,
    }
    if details:
        result["details"] = dict(details)
    return result


def _is_token_count(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0
