#!/usr/bin/env python3
"""Build or verify the public runtime-stability diagnostic suite."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hermes_dohaa.evaluation.models import EvaluationSuite  # noqa: E402


SUITE_PATH = REPO_ROOT / "examples/runtime-stability-suite-v1.json"
MANIFEST_PATH = REPO_ROOT / "examples/runtime-stability-suite-v1.manifest.json"
DOMAINS = (
    "evidence_synthesis",
    "quantitative_reconciliation",
    "structured_extraction",
    "temporal_reasoning",
)


def ref(source: str, pointer: str) -> dict[str, Any]:
    return {"op": "ref", "source": source, "pointer": pointer}


def expr(op: str, *args: dict[str, Any], **options: Any) -> dict[str, Any]:
    return {"op": op, "args": list(args), **options}


def assertion(
    assertion_id: str,
    left: dict[str, Any],
    right: dict[str, Any],
    operator: str = "equals",
) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "operator": operator,
        "left": left,
        "right": right,
    }


def result_spec(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            raise ValueError("diagnostic result arrays cannot be empty")
        first = result_spec(value[0])
        if any(result_spec(item) != first for item in value[1:]):
            raise ValueError("diagnostic result arrays must be homogeneous")
        return {"type": "array", "items": first}
    if isinstance(value, dict):
        return {
            "type": "object",
            "required": list(value),
            "additional_properties": False,
            "properties": {
                key: result_spec(item) for key, item in value.items()
            },
        }
    raise TypeError(f"unsupported result value: {type(value).__name__}")


def make_case(
    case_id: str,
    domain: str,
    objective: str,
    inputs: dict[str, Any],
    expected_result: dict[str, Any],
    semantic_assertions: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_id = f"{case_id}-source"
    contract_inputs = dict(inputs)
    contract_inputs["result_spec"] = {
        "spec_version": "2.0",
        **result_spec(expected_result),
    }
    contract_inputs["semantic_assertions"] = semantic_assertions
    return {
        "case_id": case_id,
        "domain": domain,
        "contract": {
            "schema_version": "1.0",
            "contract_id": f"runtime-stability-{case_id}",
            "objective": objective,
            "inputs": contract_inputs,
            "constraints": [
                "Use only the supplied records, rules, and reference values.",
                "Apply every stated arithmetic, filtering, ordering, and date rule before producing the result.",
                "Return exactly the keys and nested types declared by result_spec; do not add explanatory result fields.",
                f"Include an evidence item with evidence_id {evidence_id} that cites the supplied diagnostic records.",
                "Do not request actions, access external systems, or infer missing values.",
            ],
            "acceptance_criteria": [
                {
                    "criterion_id": f"{case_id}-result",
                    "description": "The structured result satisfies every visible deterministic relation.",
                    "required_evidence": [evidence_id],
                }
            ],
            "allowed_actions": [],
            "forbidden_actions": [
                "filesystem.read",
                "network.access",
                "shell.execute",
                "external.publish",
            ],
            "risk_level": "low",
            "max_attempts": 2,
            "requires_human_approval": False,
        },
        "expected_result": expected_result,
    }


def evidence_cases() -> list[dict[str, Any]]:
    inventory_inputs = {
        "ledger": {
            "opening_units": 1250,
            "receipt_batches": [180, 95, 75],
            "customer_returns": [12, 8],
            "shipment_batches": [620, 210],
            "damaged_units": 25,
            "reserved_units": 140,
        },
        "sources": [
            {"source_id": "warehouse-ledger", "role": "movement totals", "revision": 7},
            {"source_id": "damage-register", "role": "write-offs", "revision": 3},
            {"source_id": "reservation-book", "role": "committed stock", "revision": 11},
        ],
        "rules": {
            "formula": "opening + receipts + returns - shipments - damaged - reserved",
            "balanced_label": "reconciled",
            "negative_label": "investigate",
        },
        "audit_notes": [
            "Receipt batches are posted before shipment batches.",
            "Reserved units remain physically present but are not available.",
            "Damage write-offs must be deducted exactly once.",
        ],
    }
    inventory_expected = {
        "verified_available_units": 625,
        "reconciliation_status": "reconciled",
        "source_ids": [
            "warehouse-ledger",
            "damage-register",
            "reservation-book",
        ],
    }
    inventory_assertions = [
        assertion(
            "inventory-balance",
            ref("result", "/verified_available_units"),
            expr(
                "subtract",
                expr(
                    "add",
                    ref("inputs", "/ledger/opening_units"),
                    expr("sum", ref("inputs", "/ledger/receipt_batches")),
                    expr("sum", ref("inputs", "/ledger/customer_returns")),
                ),
                expr(
                    "add",
                    expr("sum", ref("inputs", "/ledger/shipment_batches")),
                    ref("inputs", "/ledger/damaged_units"),
                    ref("inputs", "/ledger/reserved_units"),
                ),
            ),
        ),
        assertion(
            "inventory-status",
            ref("result", "/reconciliation_status"),
            ref("inputs", "/rules/balanced_label"),
        ),
        assertion(
            "inventory-source-set",
            ref("result", "/source_ids"),
            expr("project", ref("inputs", "/sources"), pointer="/source_id"),
            "set_equals",
        ),
    ]

    service_inputs = {
        "telemetry": [
            {"service": "gateway", "latency_ms": 910, "error_rate": 0.070, "samples": 4800},
            {"service": "catalog", "latency_ms": 180, "error_rate": 0.008, "samples": 5100},
            {"service": "payments", "latency_ms": 1040, "error_rate": 0.090, "samples": 3950},
            {"service": "search", "latency_ms": 220, "error_rate": 0.010, "samples": 6200},
        ],
        "thresholds": {"latency_ms": 800, "error_rate": 0.05},
        "labels": {"incident_severity": "high", "normal_severity": "normal"},
        "source_catalog": [
            {"source_id": "edge-metrics", "covers": ["gateway", "catalog"]},
            {"source_id": "transaction-metrics", "covers": ["payments"]},
            {"source_id": "query-metrics", "covers": ["search"]},
        ],
        "rule_notes": [
            "A service is impacted only when both visible thresholds are met or exceeded.",
            "Keep the telemetry order when returning service identifiers.",
            "Do not average measurements between services.",
        ],
    }
    service_expected = {
        "impacted_services": ["gateway", "payments"],
        "incident_severity": "high",
        "evaluated_service_count": 4,
    }
    impacted_by_latency = expr(
        "filter",
        ref("inputs", "/telemetry"),
        ref("inputs", "/thresholds/latency_ms"),
        pointer="/latency_ms",
        comparator="greater_than_or_equal",
    )
    impacted_by_both = expr(
        "filter",
        impacted_by_latency,
        ref("inputs", "/thresholds/error_rate"),
        pointer="/error_rate",
        comparator="greater_than_or_equal",
    )
    service_assertions = [
        assertion(
            "impacted-service-order",
            ref("result", "/impacted_services"),
            expr("project", impacted_by_both, pointer="/service"),
        ),
        assertion(
            "service-count",
            ref("result", "/evaluated_service_count"),
            expr("length", ref("inputs", "/telemetry")),
        ),
        assertion(
            "incident-severity",
            ref("result", "/incident_severity"),
            ref("inputs", "/labels/incident_severity"),
        ),
    ]

    procurement_inputs = {
        "receipts": [
            {"receipt_id": "r-701", "accepted_units": 430, "rejected_units": 12, "dock": "north"},
            {"receipt_id": "r-702", "accepted_units": 275, "rejected_units": 5, "dock": "east"},
            {"receipt_id": "r-703", "accepted_units": 310, "rejected_units": 9, "dock": "north"},
        ],
        "invoice": {"invoice_id": "inv-882", "billed_units": 1020, "currency": "USD"},
        "tolerance": {"maximum_absolute_unit_difference": 2},
        "labels": {"within_tolerance": "approve", "outside_tolerance": "review"},
        "source_catalog": [
            {"source_id": "receiving-system", "authority": "accepted and rejected quantities"},
            {"source_id": "supplier-invoice", "authority": "billed quantity"},
            {"source_id": "procurement-rulebook", "authority": "difference tolerance"},
        ],
        "reconciliation_notes": [
            "Only accepted units can be matched to billed units.",
            "Rejected units are reported separately and never added to accepted units.",
            "The signed discrepancy is billed units minus accepted units.",
        ],
    }
    procurement_expected = {
        "accepted_units": 1015,
        "rejected_units": 26,
        "signed_discrepancy_units": 5,
        "decision": "review",
    }
    procurement_assertions = [
        assertion(
            "accepted-unit-total",
            ref("result", "/accepted_units"),
            expr(
                "sum",
                expr("project", ref("inputs", "/receipts"), pointer="/accepted_units"),
            ),
        ),
        assertion(
            "rejected-unit-total",
            ref("result", "/rejected_units"),
            expr(
                "sum",
                expr("project", ref("inputs", "/receipts"), pointer="/rejected_units"),
            ),
        ),
        assertion(
            "signed-procurement-discrepancy",
            ref("result", "/signed_discrepancy_units"),
            expr(
                "subtract",
                ref("inputs", "/invoice/billed_units"),
                ref("result", "/accepted_units"),
            ),
        ),
        assertion(
            "procurement-decision",
            ref("result", "/decision"),
            ref("inputs", "/labels/outside_tolerance"),
        ),
    ]

    sensor_inputs = {
        "readings": [
            {"reading_id": "s1", "value": 42, "quality": "verified"},
            {"reading_id": "s2", "value": 45, "quality": "verified"},
            {"reading_id": "s3", "value": 44, "quality": "verified"},
            {"reading_id": "s4", "value": 88, "quality": "verified"},
            {"reading_id": "s5", "value": 43, "quality": "verified"},
            {"reading_id": "s6", "value": 46, "quality": "verified"},
        ],
        "thresholds": {"anomaly_above": 70, "round_digits": 2},
        "labels": {"anomaly_action": "inspect", "normal_action": "continue"},
        "source_catalog": [
            {"source_id": "sensor-array", "revision": 12},
            {"source_id": "calibration-register", "revision": 4},
            {"source_id": "operations-thresholds", "revision": 9},
        ],
        "quality_notes": [
            "All supplied readings passed calibration and must be included in the mean.",
            "An anomaly requires a value strictly greater than the visible threshold.",
            "Return anomaly identifiers in their original observation order.",
        ],
    }
    sensor_expected = {
        "mean_value": 51.33,
        "anomaly_ids": ["s4"],
        "anomaly_count": 1,
        "recommended_action": "inspect",
    }
    anomalous = expr(
        "filter",
        ref("inputs", "/readings"),
        ref("inputs", "/thresholds/anomaly_above"),
        pointer="/value",
        comparator="greater_than",
    )
    sensor_assertions = [
        assertion(
            "sensor-mean",
            ref("result", "/mean_value"),
            expr(
                "round",
                expr(
                    "divide",
                    expr(
                        "sum",
                        expr("project", ref("inputs", "/readings"), pointer="/value"),
                    ),
                    expr("length", ref("inputs", "/readings")),
                ),
                digits=2,
            ),
        ),
        assertion(
            "sensor-anomaly-identifiers",
            ref("result", "/anomaly_ids"),
            expr("project", anomalous, pointer="/reading_id"),
        ),
        assertion(
            "sensor-anomaly-count",
            ref("result", "/anomaly_count"),
            expr("length", ref("result", "/anomaly_ids")),
        ),
        assertion(
            "sensor-action",
            ref("result", "/recommended_action"),
            ref("inputs", "/labels/anomaly_action"),
        ),
    ]

    return [
        make_case(
            "inventory-evidence-reconciliation",
            "evidence_synthesis",
            "Reconcile inventory movements from three independent evidence sources and return the available quantity, status, and complete source set.",
            inventory_inputs,
            inventory_expected,
            inventory_assertions,
        ),
        make_case(
            "service-impact-evidence",
            "evidence_synthesis",
            "Combine latency and error evidence without cross-service averaging to identify services that breach both incident thresholds.",
            service_inputs,
            service_expected,
            service_assertions,
        ),
        make_case(
            "procurement-receipt-evidence",
            "evidence_synthesis",
            "Synthesize receiving, rejection, invoice, and tolerance evidence into a signed procurement reconciliation.",
            procurement_inputs,
            procurement_expected,
            procurement_assertions,
        ),
        make_case(
            "sensor-consensus-evidence",
            "evidence_synthesis",
            "Aggregate verified sensor evidence, isolate strict-threshold anomalies, and return a bounded operational recommendation.",
            sensor_inputs,
            sensor_expected,
            sensor_assertions,
        ),
    ]


def quantitative_cases() -> list[dict[str, Any]]:
    budget_inputs = {
        "budget": {
            "initial": 450000,
            "supplements": [25000, 10000],
            "committed": [120000, 88500, 46250],
            "paid": [74000, 31500],
        },
        "rounding": {"percentage_digits": 2, "percentage_multiplier": 100},
        "labels": {"healthy": "within_plan", "exhausted": "over_plan"},
        "calculation_notes": [
            "Supplements increase the authorized budget.",
            "Committed and paid amounts are distinct uses and must both be deducted.",
            "Utilization is total use divided by total authorization, multiplied by 100.",
        ],
        "control_totals": {"line_count": 7, "currency": "USD"},
    }
    budget_expected = {
        "authorized_budget": 485000,
        "total_used": 360250,
        "remaining_budget": 124750,
        "utilization_percent": 74.28,
        "status": "within_plan",
    }
    authorized = expr(
        "add",
        ref("inputs", "/budget/initial"),
        expr("sum", ref("inputs", "/budget/supplements")),
    )
    used = expr(
        "add",
        expr("sum", ref("inputs", "/budget/committed")),
        expr("sum", ref("inputs", "/budget/paid")),
    )
    budget_assertions = [
        assertion("authorized-budget", ref("result", "/authorized_budget"), authorized),
        assertion("total-budget-use", ref("result", "/total_used"), used),
        assertion(
            "remaining-budget",
            ref("result", "/remaining_budget"),
            expr("subtract", ref("result", "/authorized_budget"), ref("result", "/total_used")),
        ),
        assertion(
            "budget-utilization",
            ref("result", "/utilization_percent"),
            expr(
                "round",
                expr(
                    "multiply",
                    expr("divide", ref("result", "/total_used"), ref("result", "/authorized_budget")),
                    ref("inputs", "/rounding/percentage_multiplier"),
                ),
                digits=2,
            ),
        ),
        assertion("budget-status", ref("result", "/status"), ref("inputs", "/labels/healthy")),
    ]

    energy_inputs = {
        "meter": {"previous_kwh": 12840, "current_kwh": 15765},
        "tariff": {
            "base_rate": 0.184,
            "peak_units": 800,
            "peak_surcharge_rate": 0.061,
            "service_fee": 32.50,
            "credit": 18.75,
        },
        "rounding": {"currency_digits": 2},
        "labels": {"currency": "USD", "billing_state": "ready"},
        "billing_notes": [
            "Usage is the current cumulative reading minus the previous reading.",
            "The peak surcharge applies only to the supplied peak-unit count.",
            "Add the service fee and subtract the credit after energy charges.",
        ],
    }
    energy_expected = {
        "usage_kwh": 2925,
        "base_charge": 538.20,
        "peak_surcharge": 48.80,
        "amount_due": 600.75,
        "billing_state": "ready",
    }
    energy_assertions = [
        assertion(
            "energy-usage",
            ref("result", "/usage_kwh"),
            expr("subtract", ref("inputs", "/meter/current_kwh"), ref("inputs", "/meter/previous_kwh")),
        ),
        assertion(
            "energy-base-charge",
            ref("result", "/base_charge"),
            expr("round", expr("multiply", ref("result", "/usage_kwh"), ref("inputs", "/tariff/base_rate")), digits=2),
        ),
        assertion(
            "energy-peak-surcharge",
            ref("result", "/peak_surcharge"),
            expr("round", expr("multiply", ref("inputs", "/tariff/peak_units"), ref("inputs", "/tariff/peak_surcharge_rate")), digits=2),
        ),
        assertion(
            "energy-total",
            ref("result", "/amount_due"),
            expr(
                "round",
                expr(
                    "subtract",
                    expr("add", ref("result", "/base_charge"), ref("result", "/peak_surcharge"), ref("inputs", "/tariff/service_fee")),
                    ref("inputs", "/tariff/credit"),
                ),
                digits=2,
            ),
        ),
        assertion("billing-state", ref("result", "/billing_state"), ref("inputs", "/labels/billing_state")),
    ]

    freight_inputs = {
        "declared_weights_kg": [128.40, 95.60, 77.25],
        "container_tares_kg": [12.50, 11.75, 9.25],
        "invoice": {"billable_weight_kg": 269.00},
        "tolerance": {"absolute_kg": 2.00},
        "labels": {"within_tolerance": "accept", "outside_tolerance": "review"},
        "rounding": {"weight_digits": 2},
        "reconciliation_notes": [
            "Gross declared weight is the sum of all package weights.",
            "Net shipment weight deducts every container tare once.",
            "Signed variance is net shipment weight minus invoice weight.",
        ],
    }
    freight_expected = {
        "gross_weight_kg": 301.25,
        "tare_weight_kg": 33.50,
        "net_weight_kg": 267.75,
        "signed_variance_kg": -1.25,
        "decision": "accept",
    }
    freight_assertions = [
        assertion("gross-freight-weight", ref("result", "/gross_weight_kg"), expr("round", expr("sum", ref("inputs", "/declared_weights_kg")), digits=2)),
        assertion("tare-freight-weight", ref("result", "/tare_weight_kg"), expr("round", expr("sum", ref("inputs", "/container_tares_kg")), digits=2)),
        assertion("net-freight-weight", ref("result", "/net_weight_kg"), expr("round", expr("subtract", ref("result", "/gross_weight_kg"), ref("result", "/tare_weight_kg")), digits=2)),
        assertion("freight-variance", ref("result", "/signed_variance_kg"), expr("round", expr("subtract", ref("result", "/net_weight_kg"), ref("inputs", "/invoice/billable_weight_kg")), digits=2)),
        assertion("freight-decision", ref("result", "/decision"), ref("inputs", "/labels/within_tolerance")),
    ]

    capacity_inputs = {
        "hourly_demand": [420, 510, 635, 590, 710, 680],
        "available_capacity": [800, 780, 760],
        "rounding": {"percentage_digits": 2, "percentage_multiplier": 100},
        "labels": {"positive_headroom": "sufficient", "negative_headroom": "insufficient"},
        "planning_notes": [
            "Use the highest observed demand as the planning peak.",
            "Use the lowest available capacity as the conservative capacity.",
            "Headroom is conservative capacity minus peak demand.",
            "Utilization divides peak demand by conservative capacity.",
        ],
        "window": {"start_hour": 8, "end_hour": 14, "timezone": "UTC"},
    }
    capacity_expected = {
        "peak_demand": 710,
        "conservative_capacity": 760,
        "headroom": 50,
        "utilization_percent": 93.42,
        "capacity_status": "sufficient",
    }
    capacity_assertions = [
        assertion("peak-demand", ref("result", "/peak_demand"), expr("max", ref("inputs", "/hourly_demand"))),
        assertion("conservative-capacity", ref("result", "/conservative_capacity"), expr("min", ref("inputs", "/available_capacity"))),
        assertion("capacity-headroom", ref("result", "/headroom"), expr("subtract", ref("result", "/conservative_capacity"), ref("result", "/peak_demand"))),
        assertion(
            "capacity-utilization",
            ref("result", "/utilization_percent"),
            expr("round", expr("multiply", expr("divide", ref("result", "/peak_demand"), ref("result", "/conservative_capacity")), ref("inputs", "/rounding/percentage_multiplier")), digits=2),
        ),
        assertion("capacity-status", ref("result", "/capacity_status"), ref("inputs", "/labels/positive_headroom")),
    ]

    return [
        make_case("program-budget-reconciliation", "quantitative_reconciliation", "Reconcile authorization, commitments, payments, remaining funds, and percentage utilization under one visible budget formula.", budget_inputs, budget_expected, budget_assertions),
        make_case("energy-billing-reconciliation", "quantitative_reconciliation", "Compute metered usage and reconcile base, peak, fee, and credit components into an exact rounded bill.", energy_inputs, energy_expected, energy_assertions),
        make_case("freight-weight-reconciliation", "quantitative_reconciliation", "Reconcile gross weights, container tares, net shipment weight, and the signed variance against an invoice.", freight_inputs, freight_expected, freight_assertions),
        make_case("capacity-headroom-reconciliation", "quantitative_reconciliation", "Derive peak demand, conservative capacity, headroom, and utilization from bounded planning series.", capacity_inputs, capacity_expected, capacity_assertions),
    ]


def extraction_cases() -> list[dict[str, Any]]:
    order_inputs = {
        "orders": [
            {"order_id": "o-101", "state": "open", "priority_rank": 2, "customer": "Aster", "amount": 4300},
            {"order_id": "o-102", "state": "closed", "priority_rank": 1, "customer": "Boreal", "amount": 1200},
            {"order_id": "o-103", "state": "open", "priority_rank": 3, "customer": "Cobalt", "amount": 2750},
            {"order_id": "o-104", "state": "open", "priority_rank": 1, "customer": "Aster", "amount": 8900},
            {"order_id": "o-105", "state": "cancelled", "priority_rank": 2, "customer": "Delta", "amount": 640},
        ],
        "selectors": {"open_state": "open"},
        "ordering": {"field": "priority_rank", "direction": "ascending"},
        "extraction_notes": [
            "Select only records whose state exactly equals the open-state selector.",
            "Sort selected records by ascending numeric priority before projecting identifiers.",
            "Return unique customers in that same selected order.",
        ],
    }
    open_orders = expr("filter", ref("inputs", "/orders"), ref("inputs", "/selectors/open_state"), pointer="/state", comparator="equals")
    sorted_open_orders = expr("sort_by", open_orders, pointer="/priority_rank", order="ascending")
    order_expected = {"open_order_ids": ["o-104", "o-101", "o-103"], "open_customers": ["Aster", "Cobalt"], "open_count": 3}
    order_assertions = [
        assertion("open-order-identifiers", ref("result", "/open_order_ids"), expr("project", sorted_open_orders, pointer="/order_id")),
        assertion("open-order-customers", ref("result", "/open_customers"), expr("unique", expr("project", sorted_open_orders, pointer="/customer"))),
        assertion("open-order-count", ref("result", "/open_count"), expr("length", ref("result", "/open_order_ids"))),
    ]

    control_inputs = {
        "controls": [
            {"control_id": "c-4", "status": "active", "severity": "critical", "due_date": "2026-08-21", "owner": "risk"},
            {"control_id": "c-7", "status": "inactive", "severity": "critical", "due_date": "2026-08-18", "owner": "security"},
            {"control_id": "c-9", "status": "active", "severity": "critical", "due_date": "2026-08-19", "owner": "security"},
            {"control_id": "c-12", "status": "active", "severity": "moderate", "due_date": "2026-08-17", "owner": "operations"},
            {"control_id": "c-15", "status": "active", "severity": "low", "due_date": "2026-08-16", "owner": "operations"},
        ],
        "selectors": {"active": "active", "critical": "critical"},
        "ordering": {"field": "due_date", "direction": "ascending"},
        "extraction_notes": [
            "Apply status and severity filters cumulatively.",
            "Sort the surviving controls by ISO due date ascending.",
            "Project control identifiers and preserve unique owners in sorted order.",
        ],
    }
    active_controls = expr("filter", ref("inputs", "/controls"), ref("inputs", "/selectors/active"), pointer="/status", comparator="equals")
    critical_controls = expr("filter", active_controls, ref("inputs", "/selectors/critical"), pointer="/severity", comparator="equals")
    sorted_controls = expr("sort_by", critical_controls, pointer="/due_date", order="ascending")
    control_expected = {"control_ids": ["c-9", "c-4"], "owners": ["security", "risk"], "control_count": 2}
    control_assertions = [
        assertion("critical-control-identifiers", ref("result", "/control_ids"), expr("project", sorted_controls, pointer="/control_id")),
        assertion("critical-control-owners", ref("result", "/owners"), expr("unique", expr("project", sorted_controls, pointer="/owner"))),
        assertion("critical-control-count", ref("result", "/control_count"), expr("length", ref("result", "/control_ids"))),
    ]

    incident_inputs = {
        "tickets": [
            {"ticket_id": "i-72", "priority": "p1", "state": "investigating", "opened_at": "2026-08-14T08:10:00Z", "system": "billing"},
            {"ticket_id": "i-74", "priority": "p2", "state": "open", "opened_at": "2026-08-14T08:05:00Z", "system": "catalog"},
            {"ticket_id": "i-77", "priority": "p1", "state": "open", "opened_at": "2026-08-14T08:25:00Z", "system": "identity"},
            {"ticket_id": "i-79", "priority": "p1", "state": "resolved", "opened_at": "2026-08-14T07:55:00Z", "system": "search"},
            {"ticket_id": "i-81", "priority": "p1", "state": "investigating", "opened_at": "2026-08-14T08:40:00Z", "system": "billing"},
        ],
        "selectors": {"priority": "p1", "excluded_state": "resolved"},
        "ordering": {"field": "opened_at", "direction": "ascending"},
        "extraction_notes": [
            "Select p1 tickets and then exclude exactly the resolved state.",
            "Sort the remaining records by timestamp ascending.",
            "Return unique affected systems in first-occurrence order.",
        ],
    }
    p1 = expr("filter", ref("inputs", "/tickets"), ref("inputs", "/selectors/priority"), pointer="/priority", comparator="equals")
    unresolved = expr("filter", p1, ref("inputs", "/selectors/excluded_state"), pointer="/state", comparator="not_equals")
    sorted_incidents = expr("sort_by", unresolved, pointer="/opened_at", order="ascending")
    incident_expected = {"ticket_ids": ["i-72", "i-77", "i-81"], "affected_systems": ["billing", "identity"], "unresolved_count": 3}
    incident_assertions = [
        assertion("unresolved-p1-identifiers", ref("result", "/ticket_ids"), expr("project", sorted_incidents, pointer="/ticket_id")),
        assertion("unresolved-p1-systems", ref("result", "/affected_systems"), expr("unique", expr("project", sorted_incidents, pointer="/system"))),
        assertion("unresolved-p1-count", ref("result", "/unresolved_count"), expr("length", ref("result", "/ticket_ids"))),
    ]

    warranty_inputs = {
        "assets": [
            {"asset_id": "a-12", "warranty_end": "2026-05-30", "region": "north", "category": "router"},
            {"asset_id": "a-18", "warranty_end": "2026-11-02", "region": "south", "category": "switch"},
            {"asset_id": "a-31", "warranty_end": "2026-08-01", "region": "north", "category": "server"},
            {"asset_id": "a-44", "warranty_end": "2026-07-12", "region": "west", "category": "router"},
            {"asset_id": "a-52", "warranty_end": "2027-01-15", "region": "west", "category": "server"},
        ],
        "cutoff": {"exclusive_date": "2026-08-15"},
        "ordering": {"field": "warranty_end", "direction": "ascending"},
        "extraction_notes": [
            "Expired means warranty_end strictly before the cutoff date.",
            "Sort expired assets by warranty_end ascending before projection.",
            "Return unique regions following that sorted asset order.",
        ],
    }
    expired = expr("filter", ref("inputs", "/assets"), ref("inputs", "/cutoff/exclusive_date"), pointer="/warranty_end", comparator="less_than")
    sorted_expired = expr("sort_by", expired, pointer="/warranty_end", order="ascending")
    warranty_expected = {"expired_asset_ids": ["a-12", "a-44", "a-31"], "regions": ["north", "west"], "expired_count": 3}
    warranty_assertions = [
        assertion("expired-asset-identifiers", ref("result", "/expired_asset_ids"), expr("project", sorted_expired, pointer="/asset_id")),
        assertion("expired-asset-regions", ref("result", "/regions"), expr("unique", expr("project", sorted_expired, pointer="/region"))),
        assertion("expired-asset-count", ref("result", "/expired_count"), expr("length", ref("result", "/expired_asset_ids"))),
    ]

    return [
        make_case("open-order-extraction", "structured_extraction", "Filter open orders, sort them by explicit priority, and project identifiers, unique customers, and count.", order_inputs, order_expected, order_assertions),
        make_case("critical-control-extraction", "structured_extraction", "Apply cumulative status and severity filters to controls, then sort and project the surviving structured records.", control_inputs, control_expected, control_assertions),
        make_case("unresolved-incident-extraction", "structured_extraction", "Extract unresolved p1 incident records, order them chronologically, and return bounded identifier and system arrays.", incident_inputs, incident_expected, incident_assertions),
        make_case("expired-warranty-extraction", "structured_extraction", "Identify assets with warranties strictly before a cutoff, sort them by expiry, and project identifiers and regions.", warranty_inputs, warranty_expected, warranty_assertions),
    ]


def temporal_cases() -> list[dict[str, Any]]:
    timeline_inputs = {
        "events": [
            {"event_id": "evt-4", "timestamp": "2026-08-14T10:42:00Z", "state": "recovering"},
            {"event_id": "evt-1", "timestamp": "2026-08-14T09:05:00Z", "state": "investigating"},
            {"event_id": "evt-5", "timestamp": "2026-08-14T11:20:00Z", "state": "restored"},
            {"event_id": "evt-2", "timestamp": "2026-08-14T09:18:00Z", "state": "identified"},
            {"event_id": "evt-3", "timestamp": "2026-08-14T09:47:00Z", "state": "mitigating"},
        ],
        "anchors": {"start": "2026-08-14T09:05:00Z", "end": "2026-08-14T11:20:00Z"},
        "labels": {"latest_state": "restored"},
        "ordering": {"field": "timestamp", "direction": "ascending"},
        "timeline_notes": [
            "Order all events by timezone-aware timestamp ascending.",
            "Elapsed minutes span the supplied start and end anchors.",
            "The latest state must correspond to the final ordered event.",
        ],
    }
    ordered_timeline = expr("sort_by", ref("inputs", "/events"), pointer="/timestamp", order="ascending")
    timeline_expected = {"ordered_event_ids": ["evt-1", "evt-2", "evt-3", "evt-4", "evt-5"], "elapsed_minutes": 135, "latest_state": "restored"}
    timeline_assertions = [
        assertion("timeline-order", ref("result", "/ordered_event_ids"), expr("project", ordered_timeline, pointer="/event_id")),
        assertion("timeline-duration", ref("result", "/elapsed_minutes"), expr("duration_minutes", ref("inputs", "/anchors/start"), ref("inputs", "/anchors/end"))),
        assertion("timeline-latest-state", ref("result", "/latest_state"), ref("inputs", "/labels/latest_state")),
    ]

    deadline_inputs = {
        "request": {"received_date": "2026-08-14", "business_days": 7},
        "calendar": {"holidays": ["2026-08-17"]},
        "milestones": {"acknowledgement_date": "2026-08-15", "review_label": "due"},
        "deadline_notes": [
            "Business-day addition starts after the received date.",
            "Saturday and Sunday are excluded.",
            "Dates listed in holidays are excluded even when they are weekdays.",
        ],
    }
    deadline_expected = {"review_deadline": "2026-08-26", "acknowledgement_date": "2026-08-15", "deadline_state": "due"}
    deadline_assertions = [
        assertion("business-review-deadline", ref("result", "/review_deadline"), expr("add_business_days", ref("inputs", "/request/received_date"), ref("inputs", "/request/business_days"), ref("inputs", "/calendar/holidays"))),
        assertion("acknowledgement-date", ref("result", "/acknowledgement_date"), ref("inputs", "/milestones/acknowledgement_date")),
        assertion("deadline-state", ref("result", "/deadline_state"), ref("inputs", "/milestones/review_label")),
    ]

    maintenance_inputs = {
        "events": [
            {"event_id": "m3", "timestamp": "2026-08-14T12:40:00Z", "state": "validation"},
            {"event_id": "m1", "timestamp": "2026-08-14T10:00:00Z", "state": "started"},
            {"event_id": "m4", "timestamp": "2026-08-14T13:45:00Z", "state": "restored"},
            {"event_id": "m2", "timestamp": "2026-08-14T10:35:00Z", "state": "upgrade"},
        ],
        "window": {"start": "2026-08-14T10:00:00Z", "end": "2026-08-14T13:45:00Z"},
        "labels": {"final_state": "restored", "window_status": "completed"},
        "ordering": {"field": "timestamp", "direction": "ascending"},
        "maintenance_notes": [
            "All timestamps use UTC and require no timezone conversion.",
            "Sort events before projecting identifiers.",
            "Window duration includes the full interval from start through end.",
        ],
    }
    ordered_maintenance = expr("sort_by", ref("inputs", "/events"), pointer="/timestamp", order="ascending")
    maintenance_expected = {"ordered_event_ids": ["m1", "m2", "m3", "m4"], "window_minutes": 225, "final_state": "restored", "window_status": "completed"}
    maintenance_assertions = [
        assertion("maintenance-order", ref("result", "/ordered_event_ids"), expr("project", ordered_maintenance, pointer="/event_id")),
        assertion("maintenance-duration", ref("result", "/window_minutes"), expr("duration_minutes", ref("inputs", "/window/start"), ref("inputs", "/window/end"))),
        assertion("maintenance-final-state", ref("result", "/final_state"), ref("inputs", "/labels/final_state")),
        assertion("maintenance-window-status", ref("result", "/window_status"), ref("inputs", "/labels/window_status")),
    ]

    sla_inputs = {
        "incident": {"opened_at": "2026-08-10T09:15:00Z", "resolved_at": "2026-08-10T12:50:00Z"},
        "sla": {"target_minutes": 180},
        "labels": {"breach_value": True, "breach_state": "breached"},
        "timeline_notes": [
            "Elapsed time is resolved_at minus opened_at in minutes.",
            "Overdue minutes are elapsed time minus the SLA target.",
            "The supplied breach label applies because the visible elapsed interval exceeds the target.",
        ],
        "audit_context": {"timezone": "UTC", "severity": "priority-1", "clock_source": "monotonic-export"},
    }
    sla_expected = {"elapsed_minutes": 215, "overdue_minutes": 35, "breached": True, "breach_state": "breached"}
    sla_assertions = [
        assertion("sla-elapsed", ref("result", "/elapsed_minutes"), expr("duration_minutes", ref("inputs", "/incident/opened_at"), ref("inputs", "/incident/resolved_at"))),
        assertion("sla-overdue", ref("result", "/overdue_minutes"), expr("subtract", ref("result", "/elapsed_minutes"), ref("inputs", "/sla/target_minutes"))),
        assertion("sla-breach-value", ref("result", "/breached"), ref("inputs", "/labels/breach_value")),
        assertion("sla-breach-state", ref("result", "/breach_state"), ref("inputs", "/labels/breach_state")),
    ]

    return [
        make_case("incident-timeline-reconstruction", "temporal_reasoning", "Reconstruct an unordered incident timeline, compute its elapsed duration, and identify the final service state.", timeline_inputs, timeline_expected, timeline_assertions),
        make_case("business-day-review-deadline", "temporal_reasoning", "Calculate a review deadline over weekends and an explicit holiday calendar while preserving supplied milestone metadata.", deadline_inputs, deadline_expected, deadline_assertions),
        make_case("maintenance-window-timeline", "temporal_reasoning", "Order maintenance events and reconcile the complete timezone-aware service window duration and final state.", maintenance_inputs, maintenance_expected, maintenance_assertions),
        make_case("sla-breach-timeline", "temporal_reasoning", "Compute elapsed and overdue minutes for an SLA interval and return the bounded breach classification.", sla_inputs, sla_expected, sla_assertions),
    ]


def build_suite_dict() -> dict[str, Any]:
    by_domain = {
        "evidence_synthesis": evidence_cases(),
        "quantitative_reconciliation": quantitative_cases(),
        "structured_extraction": extraction_cases(),
        "temporal_reasoning": temporal_cases(),
    }
    cases = [
        by_domain[domain][round_index]
        for round_index in range(4)
        for domain in DOMAINS
    ]
    return {
        "schema_version": "1.0",
        "suite_id": "public-runtime-stability-v1",
        "description": (
            "Public, reproducible runtime-stability workload for transport, "
            "timeout, isolation, and usage-telemetry diagnostics. It is not "
            "a protected benchmark and must not support generalization claims."
        ),
        "cases": cases,
    }


def build_manifest_dict(suite: EvaluationSuite) -> dict[str, Any]:
    counts = Counter(case.domain for case in suite.cases)
    return {
        "schema_version": "1.0",
        "diagnostic_id": "public-runtime-stability-v1",
        "suite_id": suite.suite_id,
        "suite_canonical_sha256": suite.sha256(),
        "case_count": len(suite.cases),
        "domains": list(DOMAINS),
        "domain_counts": {domain: counts[domain] for domain in DOMAINS},
        "case_order": [case.case_id for case in suite.cases],
        "timeout_seconds": 300,
        "smoke_repetitions": 1,
        "soak_repetitions": 3,
        "expected_requests_per_model": {
            "smoke_if_dohaa_uses_one_call": 64,
            "smoke_maximum": 80,
            "soak_if_dohaa_uses_one_call": 192,
            "soak_maximum": 240,
        },
        "scope": "runtime stability only; public development evidence",
    }


def render(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def generated_artifacts() -> dict[Path, str]:
    raw = build_suite_dict()
    suite = EvaluationSuite.from_dict(raw)
    return {
        SUITE_PATH: render(raw),
        MANIFEST_PATH: render(build_manifest_dict(suite)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the deterministic public suite and manifest",
    )
    args = parser.parse_args(argv)
    artifacts = generated_artifacts()
    if args.write:
        for path, content in artifacts.items():
            path.write_text(content, encoding="utf-8")
        print(json.dumps({"status": "written", "files": [str(path) for path in artifacts]}))
        return 0
    mismatches = [
        str(path)
        for path, expected in artifacts.items()
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]
    if mismatches:
        print(json.dumps({"status": "mismatch", "files": mismatches}))
        return 1
    suite = EvaluationSuite.from_json_file(SUITE_PATH)
    print(json.dumps({"status": "verified", "suite_id": suite.suite_id, "suite_canonical_sha256": suite.sha256(), "case_count": len(suite.cases)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
