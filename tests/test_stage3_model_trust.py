"""Tests for Stage 3: Model integration trust, failure resilience, and provenance."""

import pytest
from reviewer.contracts import (
    ApprovalRecord,
    EvidenceLocation,
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
from reviewer.model import (
    FakeModelClient,
    HybridReviewer,
    ModelConfig,
    ModelResponseValidator,
)


def _make_sample_valid_bundle() -> ReviewBundle:
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

    nodes = {
        "n_entry": WorkflowNode(
            node_id="n_entry",
            node_type="decision",
            rule_ref="R1",
            location=EvidenceLocation(line_number=10),
            branches=[
                WorkflowBranch(
                    branch_id="b1",
                    condition_text="missing(category)",
                    target_node_id="n_manual",
                ),
                WorkflowBranch(
                    branch_id="b2",
                    condition_text="otherwise",
                    target_node_id="n_r2",
                ),
            ],
        ),
        "n_manual": WorkflowNode(
            node_id="n_manual",
            node_type="action",
            action="manual_review",
            location=EvidenceLocation(line_number=20),
        ),
        "n_r2": WorkflowNode(
            node_id="n_r2",
            node_type="decision",
            rule_ref="R2",
            location=EvidenceLocation(line_number=30),
            branches=[
                WorkflowBranch(
                    branch_id="b3",
                    condition_text="urgency == 'urgent'",
                    target_node_id="n_priority",
                )
            ],
        ),
        "n_priority": WorkflowNode(
            node_id="n_priority",
            node_type="action",
            action="priority_queue",
            location=EvidenceLocation(line_number=40),
        ),
    }

    graph = WorkflowGraph(workflow_id="w1", version="1.0", entry_node_id="n_entry", nodes=nodes)

    return ReviewBundle(
        bundle_id="b_valid",
        description="Valid sample bundle",
        version="1.0",
        source_requirements=src_reqs,
        specification=spec,
        workflow_graph=graph,
        code_snippets=[],
    )


def test_hybrid_reviewer_normal_pass():
    bundle = _make_sample_valid_bundle()
    client = FakeModelClient(mode="NORMAL")
    reviewer = HybridReviewer(model_client=client)

    report = reviewer.review(bundle)
    assert report.status == ReviewStatus.PASS
    assert report.provenance["model_name"] == "reviewer-model-v1"
    assert report.provenance["token_usage"]["prompt_tokens"] > 0
    assert client.call_count == 1


def test_invariant_model_cannot_downgrade_blocker():
    # Bundle has a deliberate blocker (missing R1 in spec)
    bundle = _make_sample_valid_bundle()
    bundle.specification.rules = [bundle.specification.rules[1]]  # remove R1 from spec

    # Model in PROMPT_INJECTION_ECHO or NORMAL claiming PASS
    client = FakeModelClient(mode="PROMPT_INJECTION_ECHO")
    reviewer = HybridReviewer(model_client=client)

    report = reviewer.review(bundle)
    # The blocker MUST prevail!
    assert report.status == ReviewStatus.CHANGES_REQUIRED
    assert any("FIND-SPEC-OMISSION-R1" in f.finding_id for f in report.findings)


def test_model_hallucinated_rule_rejection():
    bundle = _make_sample_valid_bundle()
    client = FakeModelClient(mode="HALLUCINATED_RULE_REF")
    reviewer = HybridReviewer(model_client=client)

    report = reviewer.review(bundle)
    # Hallucinated rule R99 should be flagged as audit finding and discarded from main findings
    audit_findings = [f for f in report.findings if "FIND-MODEL-HALLUCINATED" in f.finding_id]
    assert len(audit_findings) > 0
    assert audit_findings[0].severity == Severity.WARNING
    assert "R99" in audit_findings[0].evidence


def test_model_timeout_and_retries():
    bundle = _make_sample_valid_bundle()
    client = FakeModelClient(mode="TIMEOUT")
    reviewer = HybridReviewer(model_client=client, config=ModelConfig(max_retries=1))

    report = reviewer.review(bundle)
    # Since model timed out and cannot complete review, it reports INCOMPLETE (or logs error audit)
    assert report.status == ReviewStatus.INCOMPLETE
    assert client.call_count == 2  # initial attempt + 1 retry
    assert any("FIND-MODEL-CALL-ERROR" in f.finding_id for f in report.findings)


def test_cache_invalidation_on_artifact_change():
    bundle = _make_sample_valid_bundle()
    client = FakeModelClient(mode="NORMAL")
    reviewer = HybridReviewer(model_client=client)

    # First call - cache miss
    rep1 = reviewer.review(bundle, use_cache=True)
    assert rep1.provenance["cache_hit"] is False
    assert client.call_count == 1

    # Second call on identical bundle - cache hit
    rep2 = reviewer.review(bundle, use_cache=True)
    assert rep2.provenance["cache_hit"] is True
    assert client.call_count == 1  # No additional model call

    # Mutate bundle (add an approval record)
    bundle_v2 = bundle.model_copy(deep=True)
    bundle_v2.approval_records.append(
        ApprovalRecord(
            change_id="CR-NEW",
            rule_ref="R2",
            field="action",
            old_value="priority_queue",
            new_value="vip_queue",
            status="APPROVED",
            approver="lead",
            timestamp="2026-04-01T00:00:00Z",
            version="v2",
        )
    )

    # Third call on modified bundle - must invalidate cache and make a new model call!
    rep3 = reviewer.review(bundle_v2, use_cache=True)
    assert rep3.provenance["cache_hit"] is False
    assert client.call_count == 2
