"""Comparative evaluation for direct, reflective, and DOHAA-controlled runs."""

from .commitment import (
    SuiteCommitment,
    SuiteCommitmentError,
    write_suite_commitment,
)
from .models import EvaluationCase, EvaluationSuite, EvaluationSuiteError
from .model_manifest import (
    ModelArtifact,
    ModelManifest,
    ModelManifestError,
    freeze_model_manifest,
    write_model_manifest,
)
from .multimodel import (
    MultimodelEvaluationError,
    RuntimeFactoryBuilder,
    aggregate_model_slot_checkpoints,
    analyze_multimodel_results,
    assess_success,
    run_model_slot_evaluation,
    run_multimodel_evaluation,
    validate_multimodel_inputs,
)
from .protocol import EvaluationProtocol, EvaluationProtocolError, ModelSlot
from .runner import (
    EvaluationCondition,
    RuntimeFactory,
    run_comparative_evaluation,
    write_evaluation_result,
)
from .statistics import analyze_unique_cases, exact_two_sided_sign_test_p

__all__ = [
    "EvaluationCase",
    "EvaluationCondition",
    "EvaluationProtocol",
    "EvaluationProtocolError",
    "EvaluationSuite",
    "EvaluationSuiteError",
    "ModelArtifact",
    "ModelManifest",
    "ModelManifestError",
    "ModelSlot",
    "MultimodelEvaluationError",
    "RuntimeFactory",
    "RuntimeFactoryBuilder",
    "SuiteCommitment",
    "SuiteCommitmentError",
    "analyze_unique_cases",
    "exact_two_sided_sign_test_p",
    "freeze_model_manifest",
    "aggregate_model_slot_checkpoints",
    "analyze_multimodel_results",
    "assess_success",
    "run_comparative_evaluation",
    "run_model_slot_evaluation",
    "run_multimodel_evaluation",
    "validate_multimodel_inputs",
    "write_evaluation_result",
    "write_model_manifest",
    "write_suite_commitment",
]
