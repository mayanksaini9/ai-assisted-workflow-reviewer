"""Tests for Stage 1: Input contracts, DSL parsing, and effective requirements derivation."""

import pytest
from reviewer.contracts import (
    ApprovalRecord,
    ComparisonCondition,
    CoverageReport,
    DSLParser,
    Duration,
    EffectiveRequirementsResolver,
    EvidenceLocation,
    InSetCondition,
    LogicalCondition,
    MissingCondition,
    RestrictedPythonASTAnalyzer,
    ReviewBundle,
    ReviewReport,
    ReviewStatus,
    Severity,
    SourceRequirement,
    Specification,
    SpecificationRule,
    TrueCondition,
    WorkflowBranch,
    WorkflowGraph,
    WorkflowNode,
)


def test_time_unit_normalization():
    d1 = Duration(30, "minutes")
    d2 = Duration(0.5, "hours")
    d3 = Duration(1800, "seconds")
    assert d1.canonical_seconds == 1800.0
    assert d2.canonical_seconds == 1800.0
    assert d3.canonical_seconds == 1800.0
    assert d1 == d2 == d3
    assert d1.to_dict()["canonical_seconds"] == 1800.0


def test_dsl_parser_patterns():
    # Missing
    c1 = DSLParser.parse("missing(category)")
    assert isinstance(c1, MissingCondition)
    assert c1.field_name == "category"
    assert c1.is_missing is True
    assert c1.evaluate({"category": None}) is True
    assert c1.evaluate({"category": "billing"}) is False

    # Comparison with time unit
    c2 = DSLParser.parse("waiting >= 30 minutes")
    assert isinstance(c2, ComparisonCondition)
    assert c2.field_name == "waiting"
    assert c2.operator == ">="
    assert isinstance(c2.value, Duration)
    assert c2.value.canonical_seconds == 1800.0
    assert c2.evaluate({"waiting": 1800.0}) is True
    assert c2.evaluate({"waiting": 1799.0}) is False

    # InSet
    c3 = DSLParser.parse("tier in ['VIP', 'platinum']")
    assert isinstance(c3, InSetCondition)
    assert c3.field_name == "tier"
    assert set(c3.values) == {"VIP", "platinum"}
    assert c3.evaluate({"tier": "vip"}) is True
    assert c3.evaluate({"tier": "gold"}) is False

    # Nested logical
    c4 = DSLParser.parse("urgency == 'urgent' and waiting >= 10 minutes")
    assert isinstance(c4, LogicalCondition)
    assert c4.operator == "AND"
    assert len(c4.children) == 2


def test_restricted_python_ast_analysis():
    code = """
def route_ticket(ticket):
    if ticket.get('category') is None:
        return 'manual_review'
    elif ticket.get('urgency') == 'urgent':
        return 'priority_queue'
    elif ticket.get('waiting_time') >= 30:
        return 'escalation'
    return 'normal_queue'
"""
    is_safe, error, tree = RestrictedPythonASTAnalyzer.validate_syntax_and_safety(code)
    assert is_safe is True
    assert error is None
    branches = RestrictedPythonASTAnalyzer.extract_branches(code)
    assert len(branches) == 4
    assert branches[0][1] == "manual_review"
    assert branches[1][1] == "priority_queue"
    assert branches[2][1] == "escalation"
    assert branches[3][1] == "normal_queue"


def test_effective_requirements_resolution():
    src_reqs = [
        SourceRequirement(
            id="R1",
            text="Missing category -> manual review.",
            anchor=EvidenceLocation(line_number=1),
            action="manual_review",
            precedence=1,
            condition_text="missing(category)",
        ),
        SourceRequirement(
            id="R2",
            text="Otherwise, urgent ticket -> priority queue.",
            anchor=EvidenceLocation(line_number=2),
            action="priority_queue",
            precedence=2,
            condition_text="urgency == 'urgent'",
        ),
        SourceRequirement(
            id="R3",
            text="Otherwise, waiting >= 30 minutes -> escalation.",
            anchor=EvidenceLocation(line_number=3),
            action="escalation",
            precedence=3,
            condition_text="waiting >= 30 minutes",
        ),
    ]

    approvals = [
        ApprovalRecord(
            change_id="CR-101",
            rule_ref="R3",
            field="threshold",
            old_value="30 minutes",
            new_value=25,
            status="APPROVED",
            approver="qa_lead@company.com",
            timestamp="2026-03-01T10:00:00Z",
            version="v1.1",
            is_top_level=True,
        )
    ]

    bundle = ReviewBundle(
        bundle_id="b1",
        description="Standard test bundle",
        version="1.0",
        source_requirements=src_reqs,
        specification=Specification(spec_id="s1", version="1.0", rules=[]),
        workflow_graph=WorkflowGraph(workflow_id="w1", version="1.0", entry_node_id="n1", nodes={}),
        code_snippets=[],
        approval_records=approvals,
    )

    effective_reqs, findings = EffectiveRequirementsResolver.resolve(bundle)
    assert len(findings) == 0
    assert len(effective_reqs) == 3
    r3 = [r for r in effective_reqs if r.rule_id == "R3"][0]
    assert "CR-101" in r3.applied_changes
    assert isinstance(r3.condition, ComparisonCondition)
    assert r3.condition.value.canonical_seconds == 1500.0  # 25 minutes


def test_contradictory_approvals_detection():
    src_reqs = [
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
            approver="qa_lead@company.com",
            timestamp="2026-03-01T10:00:00Z",
            version="v1.1",
            is_top_level=True,
        ),
        ApprovalRecord(
            change_id="CR-102",
            rule_ref="R3",
            field="threshold",
            old_value="30 minutes",
            new_value=20,
            status="APPROVED",
            approver="product_lead@company.com",
            timestamp="2026-03-01T10:05:00Z",
            version="v1.1",
            is_top_level=True,
        ),
    ]

    bundle = ReviewBundle(
        bundle_id="b_conflict",
        description="Conflicting approvals",
        version="1.0",
        source_requirements=src_reqs,
        specification=Specification(spec_id="s1", version="1.0", rules=[]),
        workflow_graph=WorkflowGraph(workflow_id="w1", version="1.0", entry_node_id="n1", nodes={}),
        code_snippets=[],
        approval_records=approvals,
    )

    effective_reqs, findings = EffectiveRequirementsResolver.resolve(bundle)
    assert any("FIND-APPR-CONFLICT" in f.finding_id for f in findings)
    assert any(f.severity == Severity.BLOCKER for f in findings)
