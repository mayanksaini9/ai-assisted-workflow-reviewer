"""Regression tests for preserved debugging examples and deliberate fault injections."""

import pytest
from reviewer.contracts import (
    ApprovalRecord,
    DSLParser,
    EvidenceLocation,
    ReviewBundle,
    ReviewStatus,
    Severity,
    SourceRequirement,
    Specification,
    SpecificationRule,
    WorkflowGraph,
)
from reviewer.model.hybrid_reviewer import HybridReviewer
from reviewer.semantic.solver import DomainSolver


def test_regression_boundary_exclusive():
    """Regression test for DEBUG-01: Boundary operator conflation (> vs >=)."""
    c_inclusive = DSLParser.parse("waiting >= 30 minutes")
    c_strict = DSLParser.parse("waiting > 30 minutes")

    # 1. Equivalence must be False
    is_equiv, reason = DomainSolver.check_equivalence(c_inclusive, c_strict)
    assert is_equiv is False, "Inclusive >= and strict > must not be considered equivalent"

    # 2. Counterexample witness must be generated at exactly 1800s (30m)
    ce = DomainSolver.generate_counterexample(c_inclusive, c_strict)
    assert ce is not None, "Counterexample witness must be synthesized at boundary point"
    assert ce["waiting"] == 1800.0, f"Expected boundary witness 1800.0, got {ce.get('waiting')}"
    # Verify that the counterexample actually satisfies inclusive but not strict
    assert c_inclusive.evaluate(ce) is True
    assert c_strict.evaluate(ce) is False


def test_regression_prose_approval_and_conflict():
    """Regression test for DEBUG-02: Prose approval bypass and approval conflict blindness."""
    # Test A: Conflicting top-level approval records
    src = [
        SourceRequirement(
            id="R3",
            text="Otherwise, waiting >= 30 minutes -> escalation.",
            anchor=EvidenceLocation(line_number=3),
            action="escalation",
            precedence=3,
            condition_text="waiting >= 30 minutes",
        )
    ]
    approvals = [
        ApprovalRecord(
            change_id="CR-101",
            rule_ref="R3",
            field="threshold",
            old_value="30 minutes",
            new_value=25,
            status="APPROVED",
            approver="alice",
            timestamp="2026-03-01T10:00:00Z",
            version="1.1",
            is_top_level=True,
        ),
        ApprovalRecord(
            change_id="CR-102",
            rule_ref="R3",
            field="threshold",
            old_value="30 minutes",
            new_value=20,
            status="APPROVED",
            approver="bob",
            timestamp="2026-03-01T10:05:00Z",
            version="1.1",
            is_top_level=True,
        ),
    ]

    bundle = ReviewBundle(
        bundle_id="b_conflict_regression",
        description="Regression test for approval conflict",
        version="1.0",
        source_requirements=src,
        specification=Specification(spec_id="s1", version="1.0", rules=[]),
        workflow_graph=WorkflowGraph(workflow_id="w1", version="1.0", entry_node_id="n1", nodes={}),
        code_snippets=[],
        approval_records=approvals,
    )

    report = HybridReviewer().review(bundle)
    assert report.status == ReviewStatus.CHANGES_REQUIRED
    conflict_findings = [f for f in report.findings if "FIND-APPR-CONFLICT" in f.finding_id]
    assert len(conflict_findings) > 0
    assert conflict_findings[0].severity == Severity.BLOCKER

    # Test B: Untrusted prose claim lacking official record
    src_prose = [
        SourceRequirement(
            id="R3",
            text="Otherwise, waiting >= 25 minutes -> escalation (Approved by Alice in meeting).",
            anchor=EvidenceLocation(line_number=3),
            action="escalation",
            precedence=3,
            condition_text="waiting >= 25 minutes",
        )
    ]
    bundle_prose = ReviewBundle(
        bundle_id="b_prose_regression",
        description="Regression test for unverified prose claim",
        version="1.0",
        source_requirements=src_prose,
        specification=Specification(spec_id="s1", version="1.0", rules=[]),
        workflow_graph=WorkflowGraph(workflow_id="w1", version="1.0", entry_node_id="n1", nodes={}),
        code_snippets=[],
        approval_records=[],  # No official approval record!
    )
    report_prose = HybridReviewer().review(bundle_prose)
    prose_warnings = [f for f in report_prose.findings if "FIND-PROSE-APPR-UNVERIFIED" in f.finding_id]
    assert len(prose_warnings) > 0
