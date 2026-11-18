"""Benchmark evaluation harness for decision correctness, finding quality, and resource metrics."""

from __future__ import annotations
import glob
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from reviewer.contracts.models import ReviewBundle, ReviewReport, ReviewStatus, Severity
from reviewer.evaluation.ground_truth import GROUND_TRUTH_REGISTRY, GroundTruthLabel, RoutingOracle


class BenchmarkRunner:
    """Runs systematic evaluation across frozen bundles and produces metrics."""

    @classmethod
    def load_bundles(cls, fixtures_dir: str = "fixtures") -> List[ReviewBundle]:
        bundles: List[ReviewBundle] = []
        files = sorted(glob.glob(os.path.join(fixtures_dir, "bundle_*.json")))
        for f in files:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                bundles.append(ReviewBundle.model_validate(data))
        return bundles

    @classmethod
    def evaluate(
        cls,
        reviewer: Any,
        bundles: Optional[List[ReviewBundle]] = None,
        fixtures_dir: str = "fixtures",
    ) -> Dict[str, Any]:
        """Evaluates reviewer on all bundles and computes evaluation metrics."""
        bundle_list = bundles or cls.load_bundles(fixtures_dir)
        results: List[Dict[str, Any]] = []

        total_runtime_ms = 0.0
        total_model_calls = 0
        total_retries = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0

        false_passes = 0
        false_blocks = 0
        correct_verdicts = 0
        incomplete_count = 0

        # Detailed split metrics
        tuning_results = []
        reserved_results = []

        findings_with_counterexamples = 0
        valid_counterexamples = 0
        unsupported_findings = 0

        for b in bundle_list:
            gt: Optional[GroundTruthLabel] = GROUND_TRUTH_REGISTRY.get(b.bundle_id)
            expected_status = gt.expected_status if gt else (b.expected_verdict or ReviewStatus.PASS)

            start_t = time.perf_counter()
            report: ReviewReport = reviewer.review(b)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            total_runtime_ms += elapsed_ms

            # Gather provenance
            prov = report.provenance or {}
            tok = prov.get("token_usage", {})
            total_prompt_tokens += tok.get("prompt_tokens", 0)
            total_completion_tokens += tok.get("completion_tokens", 0)
            total_retries += prov.get("retries", 0)
            if prov.get("model_name"):
                total_model_calls += 1

            # Decision correctness
            is_correct = report.status == expected_status
            if is_correct:
                correct_verdicts += 1

            if report.status == ReviewStatus.INCOMPLETE:
                incomplete_count += 1

            # Check False Pass (expected CHANGES_REQUIRED, got PASS)
            if expected_status == ReviewStatus.CHANGES_REQUIRED and report.status == ReviewStatus.PASS:
                false_passes += 1

            # Check False Block (expected PASS, got CHANGES_REQUIRED)
            if expected_status == ReviewStatus.PASS and report.status == ReviewStatus.CHANGES_REQUIRED:
                false_blocks += 1

            # Finding quality analysis
            for f in report.findings:
                if f.counterexample is not None:
                    findings_with_counterexamples += 1
                    # Verify counterexample validity using RoutingOracle
                    ce_ticket = f.counterexample.ticket
                    expected_route = f.counterexample.expected_route
                    oracle_route = RoutingOracle.route_policy(ce_ticket)
                    if oracle_route == expected_route:
                        valid_counterexamples += 1
                if "UNSUPPORTED" in f.finding_id or "HALLUCINATED" in f.finding_id:
                    unsupported_findings += 1

            case_result = {
                "bundle_id": b.bundle_id,
                "is_reserved": gt.is_reserved if gt else False,
                "expected_status": expected_status.value,
                "actual_status": report.status.value,
                "correct": is_correct,
                "blocker_findings_count": sum(1 for f in report.findings if f.severity == Severity.BLOCKER),
                "total_findings_count": len(report.findings),
                "counterexamples_count": sum(1 for f in report.findings if f.counterexample is not None),
                "runtime_ms": round(elapsed_ms, 2),
            }
            results.append(case_result)
            if case_result["is_reserved"]:
                reserved_results.append(case_result)
            else:
                tuning_results.append(case_result)

        total_cases = len(bundle_list)
        accuracy = round((correct_verdicts / total_cases) * 100.0, 2) if total_cases > 0 else 0.0

        # Compute precision/recall for blocker cases
        actual_blockers = [r for r in results if r["actual_status"] == "CHANGES_REQUIRED"]
        expected_blockers = [r for r in results if r["expected_status"] == "CHANGES_REQUIRED"]
        true_positives = sum(1 for r in actual_blockers if r["expected_status"] == "CHANGES_REQUIRED")

        precision = round((true_positives / len(actual_blockers)) * 100.0, 2) if actual_blockers else 100.0
        recall = round((true_positives / len(expected_blockers)) * 100.0, 2) if expected_blockers else 100.0

        summary = {
            "eval_summary": {
                "total_bundles": total_cases,
                "tuning_bundles_count": len(tuning_results),
                "reserved_bundles_count": len(reserved_results),
                "correct_verdicts": correct_verdicts,
                "accuracy_percentage": accuracy,
                "false_passes": false_passes,
                "false_blocks": false_blocks,
                "incompletes": incomplete_count,
                "precision": precision,
                "recall": recall,
            },
            "finding_quality": {
                "total_counterexamples_synthesized": findings_with_counterexamples,
                "valid_counterexamples_verified_by_oracle": valid_counterexamples,
                "unsupported_or_hallucinated_findings_flagged": unsupported_findings,
            },
            "resource_usage": {
                "total_runtime_ms": round(total_runtime_ms, 2),
                "avg_runtime_ms": round(total_runtime_ms / total_cases, 2) if total_cases else 0.0,
                "model_calls": total_model_calls,
                "retries": total_retries,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "client_mode": "fake_offline" if "fake" in str(reviewer).lower() or hasattr(reviewer, "model_client") else "deterministic",
            },
            "detailed_results": results,
        }
        return summary

    @classmethod
    def print_summary_table(cls, metrics: Dict[str, Any], reviewer_name: str) -> None:
        """Prints a clean ASCII summary table of evaluation results."""
        es = metrics["eval_summary"]
        fq = metrics["finding_quality"]
        ru = metrics["resource_usage"]

        print(f"\n{'='*70}")
        print(f" EVALUATION REPORT: {reviewer_name.upper()}")
        print(f"{'='*70}")
        print(f"Total Bundles Evaluated : {es['total_bundles']} (Tuning: {es['tuning_bundles_count']}, Reserved: {es['reserved_bundles_count']})")
        print(f"Overall Accuracy        : {es['accuracy_percentage']}% ({es['correct_verdicts']}/{es['total_bundles']})")
        print(f"False Passes (Defects->PASS)  : {es['false_passes']}")
        print(f"False Blocks (Valid->BLOCK)   : {es['false_blocks']}")
        print(f"Incomplete Verdicts           : {es['incompletes']}")
        print(f"Blocker Precision / Recall    : {es['precision']}% / {es['recall']}%")
        print(f"Counterexamples Synthesized   : {fq['total_counterexamples_synthesized']} (Oracle-verified: {fq['valid_counterexamples_verified_by_oracle']})")
        print(f"Total Wall-Clock Time         : {ru['total_runtime_ms']} ms (Avg: {ru['avg_runtime_ms']} ms/case)")
        print(f"Model Calls / Retries         : {ru['model_calls']} / {ru['retries']}")
        print(f"Tokens (Prompt / Completion)  : {ru['prompt_tokens']} / {ru['completion_tokens']}")
        print(f"{'-'*70}")
        print(f"{'Bundle ID':<44} | {'Expected':<12} | {'Actual':<12} | {'Match'}")
        print(f"{'-'*70}")
        for r in metrics["detailed_results"]:
            match_str = "OK" if r["correct"] else "MISMATCH"
            print(f"{r['bundle_id']:<44} | {r['expected_status']:<12} | {r['actual_status']:<12} | {match_str}")
        print(f"{'='*70}\n")
