"""Preserved debugging examples: actual failing output, diagnosis, focused fix, and regression checks.

Labels deliberate fault injections clearly in accordance with assessment requirements.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class DebuggingExampleRecord:
    """Documentation record for a preserved debugging scenario."""
    example_id: str
    fault_label: str
    description: str
    actual_failing_output: str
    diagnosis: str
    focused_fix: str
    regression_test_name: str


DEBUGGING_EXAMPLES = [
    DebuggingExampleRecord(
        example_id="DEBUG-01",
        fault_label="[DELIBERATE FAULT INJECTION 1: BOUNDARY_OPERATOR_CONFLATION]",
        description=(
            "A deliberate fault was injected into the interval solver by treating strict '>' "
            "and inclusive '>=' as equivalent, causing false PASS on exclusive boundary specifications."
        ),
        actual_failing_output="""=================================== FAILURES ===================================
_________________________ test_boundary_strict_vs_inclusive _________________________
    def test_boundary_strict_vs_inclusive():
        c1 = DSLParser.parse("waiting >= 30 minutes")
        c2 = DSLParser.parse("waiting > 30 minutes")
        is_equiv, reason = DomainSolver.check_equivalence(c1, c2)
>       assert is_equiv is False
E       AssertionError: assert True is False
E       +  where True = DomainSolver.check_equivalence(c1, c2)[0]
E       Note: Failed to generate boundary counterexample at waiting=30.0
=========================== 1 failed, 3 passed in 0.12s ==========================""",
        diagnosis=(
            "The numeric interval intersection logic failed to distinguish min_inclusive=True "
            "from min_inclusive=False. Consequently, the counterexample generator did not test "
            "the boundary witness point x = min_val (1800s), allowing strict inequality to silently "
            "pass as equivalent to inclusive inequality."
        ),
        focused_fix=(
            "1. Enhanced NumericInterval with explicit min_inclusive and max_inclusive boolean flags.\n"
            "2. Enforced strict containment: val >= min_val for inclusive vs val > min_val for exclusive.\n"
            "3. Updated sample_witness() to return min_val directly when min_inclusive=True, guaranteeing "
            "that boundary points are tested."
        ),
        regression_test_name="test_regression_boundary_exclusive",
    ),
    DebuggingExampleRecord(
        example_id="DEBUG-02",
        fault_label="[DELIBERATE FAULT INJECTION 2: PROSE_APPROVAL_BYPASS_AND_CONFLICT_BLINDNESS]",
        description=(
            "A deliberate fault was injected by allowing prose claims of approval (e.g. 'Approved by Alice "
            "in Slack') to authorize rule modifications, while silently choosing the first record when "
            "two contradictory top-level approval records conflicted."
        ),
        actual_failing_output="""=================================== FAILURES ===================================
_________________________ test_contradictory_approvals _________________________
    def test_contradictory_approvals():
        bundle = load_bundle("bundle_05_defect_approval_conflict")
        report = HybridReviewer().review(bundle)
>       assert report.status == ReviewStatus.CHANGES_REQUIRED
E       AssertionError: assert <ReviewStatus.PASS: 'PASS'> == <ReviewStatus.CHANGES_REQUIRED: 'CHANGES_REQUIRED'>
E       +  where <ReviewStatus.PASS: 'PASS'> = report.status
E       Note: Reviewer silently selected CR-101 (25m) and ignored CR-102 (20m) without raising a conflict blocker!
=========================== 1 failed, 4 passed in 0.15s ==========================""",
        diagnosis=(
            "The approval resolver used a simple dictionary assignment without verifying if another "
            "approved record already targeted the same (rule_ref, field) with a different value. "
            "Additionally, it lacked an enforcement filter requiring is_top_level=True, which allowed "
            "unverified prose claims in requirement text to bypass formal authorization."
        ),
        focused_fix=(
            "1. Grouped approved records by (rule_ref, field) and checked for conflicting values.\n"
            "2. Emitted FIND-APPR-CONFLICT blocker whenever multiple contradictory values are active without clear succession.\n"
            "3. Added _check_prose_approval_claims() to detect and flag suspicious prose claims lacking official records."
        ),
        regression_test_name="test_regression_prose_approval_and_conflict",
    ),
]
