"""Command Line Interface (CLI) for the AI-Assisted Workflow Reviewer."""

from __future__ import annotations
import argparse
import json
import sys
from typing import List, Optional

from reviewer.contracts.models import ReviewBundle
from reviewer.evaluation.ablation import AblatedReviewer
from reviewer.evaluation.benchmark import BenchmarkRunner
from reviewer.model.client import FakeModelClient
from reviewer.model.hybrid_reviewer import HybridReviewer
# Optional run_evaluation import handled dynamically if needed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reviewer",
        description="AI-Assisted Workflow Reviewer: Policy and Decision Workflow Verification",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: review
    review_parser = subparsers.add_parser("review", help="Review a structured workflow bundle JSON file.")
    review_parser.add_argument("bundle_path", help="Path to ReviewBundle JSON file.")
    review_parser.add_argument("--json", action="store_true", help="Output full report as JSON.")
    review_parser.add_argument("--mode", default="NORMAL", choices=["NORMAL", "TIMEOUT", "MALFORMED_JSON", "HALLUCINATED_RULE_REF"], help="Fake client test mode.")

    # Subcommand: evaluate
    eval_parser = subparsers.add_parser("evaluate", help="Run offline evaluation benchmark across frozen bundles.")
    eval_parser.add_argument("--fixtures-dir", default="fixtures", help="Directory containing review bundles.")
    eval_parser.add_argument("--output-dir", default="reports", help="Directory for JSON reports.")

    # Subcommand: ablate
    ablate_parser = subparsers.add_parser("ablate", help="Run controlled ablation study.")

    args = parser.parse_args(argv)

    if args.command == "review":
        try:
            with open(args.bundle_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            bundle = ReviewBundle.model_validate(data)
        except Exception as e:
            print(f"Error loading review bundle '{args.bundle_path}': {e}", file=sys.stderr)
            return 1

        client = FakeModelClient(mode=args.mode)
        reviewer = HybridReviewer(model_client=client)
        report = reviewer.review(bundle)

        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            print("=" * 70)
            print(f" REVIEW VERDICT: {report.status.value}")
            print("=" * 70)
            print(f"Summary: {report.summary}\n")
            print(f"Coverage: {report.coverage.completeness_ratio * 100:.1f}% ({len(report.coverage.reviewed_requirements)}/{report.coverage.total_source_requirements} requirements reviewed)")
            if report.coverage.unsupported_elements:
                print("Unsupported Scope Disclosed:")
                for u in report.coverage.unsupported_elements:
                    print(f" - Rule {u['rule_id']}: {u['reason']}")
            print(f"\nFindings ({len(report.findings)} total):")
            for idx, f in enumerate(report.findings, 1):
                rule_str = f" [Rule: {f.rule_ref}]" if f.rule_ref else ""
                print(f"  {idx}. [{f.severity.value}]{rule_str} ({f.artifact}) {f.message}")
                print(f"     Location: {f.location.display()}")
                print(f"     Evidence: {f.evidence}")
                if f.correction:
                    print(f"     Correction: {f.correction}")
                if f.counterexample:
                    ce = f.counterexample
                    print(f"     * Counterexample Ticket: {json.dumps(ce.ticket)}")
                    print(f"       Expected route: {ce.expected_route} | Actual route: {ce.actual_route}")
            print("=" * 70)
        return 0

    if args.command == "evaluate":
        from reviewer.evaluation.baseline import BaselineReviewer
        bundles = BenchmarkRunner.load_bundles(args.fixtures_dir)
        final_reviewer = HybridReviewer(model_client=FakeModelClient(mode="NORMAL"))
        metrics = BenchmarkRunner.evaluate(final_reviewer, bundles=bundles)
        BenchmarkRunner.print_summary_table(metrics, "Final Hybrid Reviewer")
        return 0

    if args.command == "ablate":
        from reviewer.evaluation.ablation import AblatedReviewer
        bundles = BenchmarkRunner.load_bundles("fixtures")
        ablated = AblatedReviewer()
        metrics = BenchmarkRunner.evaluate(ablated, bundles=bundles)
        BenchmarkRunner.print_summary_table(metrics, "Controlled Ablation (AST Disabled)")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
