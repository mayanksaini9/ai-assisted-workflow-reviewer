"""Tests for Stage 4: Controlled evaluation harness, baseline comparison, and ablation."""

import pytest
from reviewer.contracts import ReviewStatus
from reviewer.evaluation.ablation import AblatedReviewer
from reviewer.evaluation.baseline import BaselineReviewer
from reviewer.evaluation.benchmark import BenchmarkRunner
from reviewer.model.client import FakeModelClient
from reviewer.model.hybrid_reviewer import HybridReviewer


def test_baseline_evaluation_accuracy():
    bundles = BenchmarkRunner.load_bundles("fixtures")
    assert len(bundles) == 16

    baseline_metrics = BenchmarkRunner.evaluate(BaselineReviewer, bundles=bundles)
    # Baseline v0 fails on time unit equivalences (0.5h vs 30m) and AST code disagreements
    assert baseline_metrics["eval_summary"]["false_passes"] > 0 or baseline_metrics["eval_summary"]["false_blocks"] > 0
    assert baseline_metrics["eval_summary"]["accuracy_percentage"] < 100.0


def test_final_reviewer_evaluation_accuracy():
    bundles = BenchmarkRunner.load_bundles("fixtures")
    final_client = FakeModelClient(mode="NORMAL")
    final_reviewer = HybridReviewer(model_client=final_client)

    final_metrics = BenchmarkRunner.evaluate(final_reviewer, bundles=bundles)
    # Final reviewer achieves 100% decision correctness across all 16 bundles
    assert final_metrics["eval_summary"]["accuracy_percentage"] == 100.0
    assert final_metrics["eval_summary"]["false_passes"] == 0
    assert final_metrics["eval_summary"]["false_blocks"] == 0
    assert final_metrics["eval_summary"]["incompletes"] == 3


def test_ablation_demonstrates_ast_importance():
    bundles = BenchmarkRunner.load_bundles("fixtures")
    b8 = [b for b in bundles if b.bundle_id == "bundle_08_defect_code_disagreement"][0]

    # Final reviewer detects the code logic mismatch
    final_reviewer = HybridReviewer(model_client=FakeModelClient(mode="NORMAL"))
    rep_final = final_reviewer.review(b8)
    assert rep_final.status == ReviewStatus.CHANGES_REQUIRED
    assert any("FIND-CODE-LOGIC-MISMATCH" in f.finding_id for f in rep_final.findings)

    # Ablated reviewer (AST inspection disabled) produces a False Pass!
    ablated_reviewer = AblatedReviewer()
    rep_ablated = ablated_reviewer.review(b8)
    assert rep_ablated.status == ReviewStatus.PASS
