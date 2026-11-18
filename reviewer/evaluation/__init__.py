"""Evaluation package: benchmark, ground truth, baseline, ablation, and regressions."""

from reviewer.evaluation.ablation import AblatedReviewer
from reviewer.evaluation.baseline import BaselineReviewer
from reviewer.evaluation.benchmark import BenchmarkRunner
from reviewer.evaluation.generate_fixtures import generate_all_bundles, write_fixtures_to_disk
from reviewer.evaluation.ground_truth import GROUND_TRUTH_REGISTRY, GroundTruthLabel, RoutingOracle
from reviewer.evaluation.regressions import DEBUGGING_EXAMPLES, DebuggingExampleRecord

__all__ = [
    "AblatedReviewer",
    "BaselineReviewer",
    "BenchmarkRunner",
    "DEBUGGING_EXAMPLES",
    "DebuggingExampleRecord",
    "GROUND_TRUTH_REGISTRY",
    "GroundTruthLabel",
    "RoutingOracle",
    "generate_all_bundles",
    "write_fixtures_to_disk",
]
