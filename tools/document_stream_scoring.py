"""Reference-side scoring; never place expectations in a model-visible prompt.

Exact structured values are intentional. Natural-language equivalence, authority
between sources and reference annotation quality require separate evaluation.
"""

from dataclasses import dataclass

from tools.document_stream import DocumentEvent, identifier, nonnegative_integer


def citation(value):
    if type(value) is not dict or set(value) != {"source_id", "revision"}:
        raise ValueError("invalid citation")
    if not identifier(value["source_id"]) or not nonnegative_integer(value["revision"]) or value["revision"] == 0:
        raise ValueError("invalid citation identity")
    return value["source_id"], value["revision"]


@dataclass(frozen=True)
class ReferenceFact:
    key: str
    value: str
    acceptable_evidence: tuple[tuple[tuple[str, int], ...], ...]
    critical: bool = True

    def __post_init__(self):
        if not identifier(self.key) or type(self.value) is not str or not self.value or len(self.value) > 512:
            raise ValueError("invalid reference fact")
        if type(self.critical) is not bool or type(self.acceptable_evidence) is not tuple or not 1 <= len(self.acceptable_evidence) <= 16:
            raise ValueError("invalid reference evidence alternatives")
        alternatives = set()
        for group in self.acceptable_evidence:
            if type(group) is not tuple or not 1 <= len(group) <= 16:
                raise ValueError("invalid reference evidence group")
            checked = []
            for pair in group:
                if type(pair) is not tuple or len(pair) != 2:
                    raise ValueError("invalid reference citation")
                checked.append(citation({"source_id": pair[0], "revision": pair[1]}))
            frozen = frozenset(checked)
            if len(frozen) != len(checked) or frozen in alternatives:
                raise ValueError("duplicate reference evidence")
            alternatives.add(frozen)


def score_report(report: object, expected: list[ReferenceFact], active_sources: dict[str, DocumentEvent]) -> dict:
    """Return finite counts; bad references raise, malformed outputs score zero.

    Missing facts include every reference not correctly and supportably reported.
    Error categories can overlap, so they must not be added as disjoint totals.
    No empty-output loophole: an abstention misses every required reference.
    """
    if len(expected) > 128 or any(type(ref) is not ReferenceFact for ref in expected):
        raise ValueError("invalid reference set")
    references = {ref.key: ref for ref in expected}
    if len(references) != len(expected):
        raise ValueError("duplicate reference key")
    available = {(key, event.revision) for key, event in active_sources.items()}
    for ref in expected:
        if not any(set(group) <= available for group in ref.acceptable_evidence):
            raise ValueError("reference evidence unavailable at checkpoint")
    result = {"valid_format": False, "task_success": False, "expected_facts": len(expected),
              "reported_facts": 0, "correct_facts": 0, "missing_facts": len(expected),
              "missing_critical_facts": sum(ref.critical for ref in expected),
              "wrong_value_facts": 0, "extra_facts": 0, "unsupported_facts": 0,
              "stale_or_unknown_evidence_facts": 0}
    parsed = {}
    try:
        if type(report) is not dict or set(report) != {"facts"}:
            raise ValueError("invalid report")
        if type(report["facts"]) is not list or len(report["facts"]) > 128:
            raise ValueError("invalid fact collection")
        for fact in report["facts"]:
            if type(fact) is not dict or set(fact) != {"key", "value", "evidence"}:
                raise ValueError("invalid fact")
            key, value, evidence = fact["key"], fact["value"], fact["evidence"]
            if not identifier(key) or key in parsed or type(value) is not str or not value or len(value) > 512:
                raise ValueError("invalid fact identity or value")
            if type(evidence) is not list or len(evidence) > 16:
                raise ValueError("invalid evidence collection")
            pairs = [citation(item) for item in evidence]
            if len(set(pairs)) != len(pairs):
                raise ValueError("duplicate citation")
            parsed[key] = value, frozenset(pairs)
    except ValueError:
        return result
    result["valid_format"] = True
    result["reported_facts"] = len(parsed)
    correct = set()
    for key, (value, evidence) in parsed.items():
        if not evidence <= available:
            result["stale_or_unknown_evidence_facts"] += 1
        ref = references.get(key)
        if ref is None:
            result["extra_facts"] += 1
            continue
        value_ok = value == ref.value
        evidence_ok = evidence <= available and evidence in [frozenset(group) for group in ref.acceptable_evidence]
        result["wrong_value_facts"] += not value_ok
        result["unsupported_facts"] += not evidence_ok
        if value_ok and evidence_ok:
            correct.add(key)
    result["correct_facts"] = len(correct)
    result["missing_facts"] = len(expected) - len(correct)
    result["missing_critical_facts"] = sum(ref.critical for ref in expected if ref.key not in correct)
    result["task_success"] = len(correct) == len(expected) and len(parsed) == len(expected)
    return result
