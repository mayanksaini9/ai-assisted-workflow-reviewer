"""Tests for Stage 2: Deterministic semantic checks, constraint solving, and counterexamples."""

import pytest
from reviewer.contracts import (
    CodeSnippet,
    ComparisonCondition,
    DSLParser,
    Duration,
    EvidenceLocation,
    MissingCondition,
    ReviewBundle,
    ReviewStatus,
    Severity,
    SourceRequirement,
    Specification,
    SpecificationRule,
    WorkflowBranch,
    WorkflowGraph,
    WorkflowNode,
)
from reviewer.semantic import (
    CodeSnippetAnalyzer,
    DeterministicReviewer,
    DomainSolver,
    WorkflowGraphAnalyzer,
)


def test_solver_boundary_and_equivalence():
    # waiting >= 30 minutes vs waiting > 30 minutes
    c1 = DSLParser.parse("waiting >= 30 minutes")
    c2 = DSLParser.parse("waiting > 30 minutes")
    is_equiv, reason = DomainSolver.check_equivalence(c1, c2)
    assert is_equiv is False

    # Counterexample should be waiting == 1800s (30m)
    ce = DomainSolver.generate_counterexample(c1, c2)
    assert ce is not None
    assert ce["waiting"] == 1800.0


def test_precedence_inversion_counterexample():
    # R1: missing category -> manual review
    # R2: urgency == 'urgent' -> priority queue
    c1 = DSLParser.parse("missing(category)")
    c2 = DSLParser.parse("urgency == 'urgent'")
    ce = DomainSolver.generate_precedence_counterexample(c1, c2)
    assert ce is not None
    assert ce["category"] is None
    assert ce["urgency"] == "urgent"


def test_workflow_graph_precedence_inversion():
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
    ]

    # Construct workflow where node for R2 is evaluated BEFORE node for R1!
    nodes = {
        "n_entry": WorkflowNode(
            node_id="n_entry",
            node_type="decision",
            rule_ref="R2",  # R2 evaluated first!
            location=EvidenceLocation(line_number=10),
            branches=[
                WorkflowBranch(
                    branch_id="b_urgent",
                    condition_text="urgency == 'urgent'",
                    target_node_id="n_priority",
                ),
                WorkflowBranch(
                    branch_id="b_other",
                    condition_text="otherwise",
                    target_node_id="n_r1",
                ),
            ],
        ),
        "n_priority": WorkflowNode(
            node_id="n_priority",
            node_type="action",
            action="priority_queue",
            location=EvidenceLocation(line_number=20),
        ),
        "n_r1": WorkflowNode(
            node_id="n_r1",
            node_type="decision",
            rule_ref="R1",
            location=EvidenceLocation(line_number=30),
            branches=[
                WorkflowBranch(
                    branch_id="b_missing",
                    condition_text="missing(category)",
                    target_node_id="n_manual",
                ),
                WorkflowBranch(
                    branch_id="b_normal",
                    condition_text="otherwise",
                    target_node_id="n_normal",
                ),
            ],
        ),
        "n_manual": WorkflowNode(
            node_id="n_manual",
            node_type="action",
            action="manual_review",
            location=EvidenceLocation(line_number=40),
        ),
        "n_normal": WorkflowNode(
            node_id="n_normal",
            node_type="action",
            action="normal_queue",
            location=EvidenceLocation(line_number=50),
        ),
    }

    graph = WorkflowGraph(workflow_id="w1", version="1.0", entry_node_id="n_entry", nodes=nodes)
    spec = Specification(
        spec_id="s1",
        version="1.0",
        rules=[
            SpecificationRule(
                rule_id="R1",
                description="Missing category -> manual review",
                condition_text="missing(category)",
                action="manual_review",
                precedence=1,
                location=EvidenceLocation(line_number=1),
            ),
            SpecificationRule(
                rule_id="R2",
                description="Urgent ticket -> priority queue",
                condition_text="urgency == 'urgent'",
                action="priority_queue",
                precedence=2,
                location=EvidenceLocation(line_number=2),
            ),
        ],
    )

    bundle = ReviewBundle(
        bundle_id="b_prec_inversion",
        description="Graph precedence inversion",
        version="1.0",
        source_requirements=src_reqs,
        specification=spec,
        workflow_graph=graph,
        code_snippets=[],
    )

    report = DeterministicReviewer.review(bundle)
    assert report.status == ReviewStatus.CHANGES_REQUIRED
    precedence_findings = [f for f in report.findings if "FIND-GRAPH-PRECEDENCE" in f.finding_id]
    assert len(precedence_findings) > 0
    f = precedence_findings[0]
    assert f.counterexample is not None
    assert f.counterexample.expected_route == "manual_review"
    assert f.counterexample.actual_route == "priority_queue"


def test_code_disagreement_detection():
    src_reqs = [
        SourceRequirement(
            id="R3",
            text="Otherwise, waiting >= 30 minutes -> escalation.",
            anchor=EvidenceLocation(line_number=3),
            action="escalation",
            precedence=3,
            condition_text="waiting_time >= 30 minutes",
        )
    ]
    spec = Specification(
        spec_id="s1",
        version="1.0",
        rules=[
            SpecificationRule(
                rule_id="R3",
                description="Waiting >= 30 min -> escalation",
                condition_text="waiting_time >= 30 minutes",
                action="escalation",
                precedence=3,
                location=EvidenceLocation(line_number=3),
            )
        ],
    )
    graph = WorkflowGraph(
        workflow_id="w1",
        version="1.0",
        entry_node_id="n_r3",
        nodes={
            "n_r3": WorkflowNode(
                node_id="n_r3",
                node_type="decision",
                rule_ref="R3",
                location=EvidenceLocation(line_number=10),
                branches=[
                    WorkflowBranch(
                        branch_id="b_esc",
                        condition_text="waiting_time >= 30 minutes",
                        target_node_id="n_esc",
                    )
                ],
            ),
            "n_esc": WorkflowNode(
                node_id="n_esc",
                node_type="action",
                action="escalation",
                location=EvidenceLocation(line_number=20),
            ),
        },
    )

    # Disagreeing code: comment says R3, but code does > 30 minutes!
    code = """
def handle_r3(ticket):
    # Implements R3: waiting >= 30 minutes -> escalation
    if ticket.get('waiting_time') > 30:
        return 'escalation'
    return 'normal_queue'
"""
    snippets = [
        CodeSnippet(
            snippet_id="snip_r3",
            rule_ref="R3",
            code=code,
            location=EvidenceLocation(file_path="routing.py", line_number=15),
        )
    ]

    bundle = ReviewBundle(
        bundle_id="b_code_mismatch",
        description="Code boundary disagreement",
        version="1.0",
        source_requirements=src_reqs,
        specification=spec,
        workflow_graph=graph,
        code_snippets=snippets,
    )

    report = DeterministicReviewer.review(bundle)
    assert report.status == ReviewStatus.CHANGES_REQUIRED
    code_findings = [f for f in report.findings if "FIND-CODE-LOGIC-MISMATCH" in f.finding_id]
    assert len(code_findings) > 0
    f = code_findings[0]
    assert f.rule_ref == "R3"
    assert f.counterexample is not None
