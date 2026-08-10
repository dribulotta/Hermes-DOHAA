"""Dependency-free statistics for paired evaluation results."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


_CONDITIONS = ("direct", "self_reflection", "dohaa")
_COMPARISONS = (
    ("dohaa_vs_direct", "dohaa", "direct"),
    ("dohaa_vs_self_reflection", "dohaa", "self_reflection"),
    ("self_reflection_vs_direct", "self_reflection", "direct"),
)


def analyze_unique_cases(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in case_results:
        grouped[str(trial["case_id"])].append(trial)
    if not grouped:
        raise ValueError("statistical analysis requires at least one case")

    rates: dict[str, dict[str, float]] = {
        condition: {} for condition in _CONDITIONS
    }
    for case_id, trials in sorted(grouped.items()):
        for condition in _CONDITIONS:
            passed = sum(
                _outcome_passed(trial["conditions"][condition])
                for trial in trials
            )
            rates[condition][case_id] = passed / len(trials)

    condition_stats = {
        condition: _condition_statistics(condition_rates)
        for condition, condition_rates in rates.items()
    }
    paired = {
        name: _paired_statistics(rates[left], rates[right])
        for name, left, right in _COMPARISONS
    }
    return {
        "unit_of_analysis": "unique_case",
        "unique_cases": len(grouped),
        "condition_statistics": condition_stats,
        "paired_sign_tests": paired,
        "interpretation": (
            "Repetitions are nested within each case. Exact sign tests count "
            "unique cases, not repeated trials, as independent units."
        ),
    }


def exact_two_sided_sign_test_p(wins: int, losses: int) -> float | None:
    if min(wins, losses) < 0:
        raise ValueError("wins and losses cannot be negative")
    discordant = wins + losses
    if discordant == 0:
        return None
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(wins, losses) + 1)
    ) / (2**discordant)
    return round(min(1.0, 2 * tail), 12)


def _condition_statistics(case_rates: dict[str, float]) -> dict[str, Any]:
    strict_passes = sum(rate == 1.0 for rate in case_rates.values())
    count = len(case_rates)
    return {
        "unique_cases": count,
        "strict_passes": strict_passes,
        "strict_pass_rate": round(strict_passes / count, 6),
        "strict_pass_rate_wilson_95_ci": list(
            _wilson_interval(strict_passes, count)
        ),
        "mean_repetition_pass_rate": round(
            sum(case_rates.values()) / count,
            6,
        ),
    }


def _paired_statistics(
    left_rates: dict[str, float],
    right_rates: dict[str, float],
) -> dict[str, Any]:
    if set(left_rates) != set(right_rates):
        raise ValueError("paired conditions must contain the same case IDs")
    wins = sum(left_rates[key] > right_rates[key] for key in left_rates)
    losses = sum(left_rates[key] < right_rates[key] for key in left_rates)
    ties = len(left_rates) - wins - losses
    differences = [
        left_rates[key] - right_rates[key]
        for key in sorted(left_rates)
    ]
    return {
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "discordant_cases": wins + losses,
        "mean_pass_rate_difference": round(
            sum(differences) / len(differences),
            6,
        ),
        "exact_two_sided_sign_test_p": exact_two_sided_sign_test_p(
            wins,
            losses,
        ),
    }


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("Wilson interval requires at least one observation")
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + (z * z / total)
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return (
        round(max(0.0, center - margin), 6),
        round(min(1.0, center + margin), 6),
    )


def _outcome_passed(outcome: dict[str, Any]) -> int:
    score = outcome.get("final_score")
    return int(bool(score and score.get("all_gates_passed")))
