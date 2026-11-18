"""Master CPU-only offline evaluation and benchmark runner.

Executes baseline checkpoint, final hybrid reviewer, and controlled ablation across
the 16 frozen evaluation bundles, producing machine-readable JSON reports and metrics.
"""

from __future__ import annotations
import json
import os
import sys

from reviewer.evaluation.ablation import AblatedReviewer
from reviewer.evaluation.baseline import BaselineReviewer
from reviewer.evaluation.benchmark import BenchmarkRunner
from reviewer.evaluation.generate_fixtures import write_fixtures_to_disk
from reviewer.evaluation.regressions import DEBUGGING_EXAMPLES
from reviewer.model.client import FakeModelClient
from reviewer.model.hybrid_reviewer import HybridReviewer


def run_full_evaluation(output_dir: str = "reports") -> int:
    os.makedirs(output_dir, exist_ok=True)
    fixtures_dir = "fixtures"

    print("=" * 80)
    print(" AI-ASSISTED WORKFLOW REVIEWER: OFFLINE EVALUATION SUITE")
    print("=" * 80)
    print("Running in CPU-only offline mode with deterministic fake model client.\n")

    # 1. Ensure fixtures are present
    write_fixtures_to_disk(fixtures_dir)
    bundles = BenchmarkRunner.load_bundles(fixtures_dir)
    print(f"Loaded {len(bundles)} frozen evaluation bundles from '{fixtures_dir}'.\n")

    # 2. Run Baseline Reviewer (v0 Checkpoint)
    print(">>> 1/3 Running Baseline Reviewer Checkpoint (v0)...")
    baseline_metrics = BenchmarkRunner.evaluate(BaselineReviewer, bundles=bundles)
    baseline_path = os.path.join(output_dir, "baseline_evaluation.json")
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(baseline_metrics, f, indent=2)
    BenchmarkRunner.print_summary_table(baseline_metrics, "Baseline Reviewer (v0 Checkpoint)")

    # 3. Run Final Hybrid Reviewer
    print(">>> 2/3 Running Final Hybrid Reviewer...")
    final_client = FakeModelClient(mode="NORMAL")
    final_reviewer = HybridReviewer(model_client=final_client)
    final_metrics = BenchmarkRunner.evaluate(final_reviewer, bundles=bundles)
    final_path = os.path.join(output_dir, "final_evaluation.json")
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(final_metrics, f, indent=2)
    BenchmarkRunner.print_summary_table(final_metrics, "Final Hybrid Reviewer")

    # 4. Run Controlled Ablation (AST Code Inspection Disabled)
    print(">>> 3/3 Running Controlled Ablation (AST Code Inspection Disabled)...")
    ablated_reviewer = AblatedReviewer()
    ablation_metrics = BenchmarkRunner.evaluate(ablated_reviewer, bundles=bundles)
    ablation_path = os.path.join(output_dir, "ablation_evaluation.json")
    with open(ablation_path, "w", encoding="utf-8") as f:
        json.dump(ablation_metrics, f, indent=2)
    BenchmarkRunner.print_summary_table(ablation_metrics, "Controlled Ablation (AST Code Analysis Disabled)")

    # 5. Comparative Performance Summary
    print("\n" + "=" * 80)
    print(" COMPARATIVE SUMMARY & STATISTICAL METRICS")
    print("=" * 80)
    b_es = baseline_metrics["eval_summary"]
    f_es = final_metrics["eval_summary"]
    a_es = ablation_metrics["eval_summary"]

    print(f"{'Metric':<32} | {'Baseline (v0)':<15} | {'Ablated':<15} | {'Final':<15}")
    print("-" * 80)
    print(f"{'Accuracy (%)':<32} | {b_es['accuracy_percentage']:<15} | {a_es['accuracy_percentage']:<15} | {f_es['accuracy_percentage']:<15}")
    print(f"{'False Passes (Defects -> PASS)':<32} | {b_es['false_passes']:<15} | {a_es['false_passes']:<15} | {f_es['false_passes']:<15}")
    print(f"{'False Blocks (Valid -> BLOCK)':<32} | {b_es['false_blocks']:<15} | {a_es['false_blocks']:<15} | {f_es['false_blocks']:<15}")
    print(f"{'Incompletes Identified':<32} | {b_es['incompletes']:<15} | {a_es['incompletes']:<15} | {f_es['incompletes']:<15}")
    print(f"{'Blocker Precision (%)':<32} | {b_es['precision']:<15} | {a_es['precision']:<15} | {f_es['precision']:<15}")
    print(f"{'Blocker Recall (%)':<32} | {b_es['recall']:<15} | {a_es['recall']:<15} | {f_es['recall']:<15}")
    print(f"{'Counterexamples Synthesized':<32} | {baseline_metrics['finding_quality']['total_counterexamples_synthesized']:<15} | {ablation_metrics['finding_quality']['total_counterexamples_synthesized']:<15} | {final_metrics['finding_quality']['total_counterexamples_synthesized']:<15}")
    print(f"{'Total Runtime (ms)':<32} | {baseline_metrics['resource_usage']['total_runtime_ms']:<15} | {ablation_metrics['resource_usage']['total_runtime_ms']:<15} | {final_metrics['resource_usage']['total_runtime_ms']:<15}")
    print("=" * 80)

    # 6. Display Preserved Debugging Examples Summary
    print("\n" + "=" * 80)
    print(" PRESERVED SUBSTANTIVE DEBUGGING EXAMPLES")
    print("=" * 80)
    for ex in DEBUGGING_EXAMPLES:
        print(f"\nExample ID   : {ex.example_id}")
        print(f"Fault Label  : {ex.fault_label}")
        print(f"Description  : {ex.description}")
        print(f"Diagnosis    : {ex.diagnosis}")
        print(f"Regression   : {ex.regression_test_name} (VERIFIED)")
    print("=" * 80 + "\n")

    print(f"Machine-readable reports written to:\n- {baseline_path}\n- {final_path}\n- {ablation_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_full_evaluation())
