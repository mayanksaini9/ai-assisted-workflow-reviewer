"""Independent ground truth routing oracle and verification labels.

Provides independent semantic validation outside the reviewer logic.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from reviewer.contracts.models import ReviewStatus


@dataclass(frozen=True)
class GroundTruthLabel:
    """Independent ground-truth label for evaluation bundles."""
    bundle_id: str
    expected_status: ReviewStatus
    expected_defects: List[str]
    rationale: str
    is_reserved: bool = False


# Frozen ground truth labels established independently prior to evaluation
GROUND_TRUTH_REGISTRY: Dict[str, GroundTruthLabel] = {
    "bundle_01_valid_standard": GroundTruthLabel(
        bundle_id="bundle_01_valid_standard",
        expected_status=ReviewStatus.PASS,
        expected_defects=[],
        rationale="Standard 4-tier routing policy completely aligned across source, spec, workflow, and code.",
        is_reserved=False,
    ),
    "bundle_02_valid_reordered_records": GroundTruthLabel(
        bundle_id="bundle_02_valid_reordered_records",
        expected_status=ReviewStatus.PASS,
        expected_defects=[],
        rationale="Semantically identical to Bundle 01 with serialized JSON entries reordered.",
        is_reserved=False,
    ),
    "bundle_03_valid_unusual_formulation": GroundTruthLabel(
        bundle_id="bundle_03_valid_unusual_formulation",
        expected_status=ReviewStatus.PASS,
        expected_defects=[],
        rationale="Uses equivalent time units (0.5 hours) and negated conditions; logic is sound.",
        is_reserved=False,
    ),
    "bundle_04_defect_unapproved_threshold": GroundTruthLabel(
        bundle_id="bundle_04_defect_unapproved_threshold",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-SPEC-CONDITION-MISMATCH-R3"],
        rationale="Specification altered threshold to 25 minutes without top-level approval record.",
        is_reserved=False,
    ),
    "bundle_05_defect_approval_conflict": GroundTruthLabel(
        bundle_id="bundle_05_defect_approval_conflict",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-APPR-CONFLICT-R3-threshold"],
        rationale="Two contradictory approved change requests exist for rule R3 threshold.",
        is_reserved=False,
    ),
    "bundle_06_defect_omitted_source_req": GroundTruthLabel(
        bundle_id="bundle_06_defect_omitted_source_req",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-SPEC-OMISSION-R1"],
        rationale="Specification and workflow completely omit mandatory source requirement R1.",
        is_reserved=False,
    ),
    "bundle_07_defect_precedence_inversion": GroundTruthLabel(
        bundle_id="bundle_07_defect_precedence_inversion",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-GRAPH-PRECEDENCE-R1-R2"],
        rationale="Workflow graph evaluates R2 (urgent) before R1 (missing category).",
        is_reserved=False,
    ),
    "bundle_08_defect_code_disagreement": GroundTruthLabel(
        bundle_id="bundle_08_defect_code_disagreement",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-CODE-LOGIC-MISMATCH-snip_r3-R3"],
        rationale="Python code uses > instead of >= 30, disagreeing with metadata.",
        is_reserved=False,
    ),
    "bundle_09_defect_dead_branch_interaction": GroundTruthLabel(
        bundle_id="bundle_09_defect_dead_branch_interaction",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-RULE-SHADOWED-R3"],
        rationale="An unconditional fallback rule placed at precedence 2 shadows rule R3.",
        is_reserved=False,
    ),
    "bundle_10_incomplete_unsupported_language": GroundTruthLabel(
        bundle_id="bundle_10_incomplete_unsupported_language",
        expected_status=ReviewStatus.INCOMPLETE,
        expected_defects=[],
        rationale="Requirement contains unparseable ambiguous natural language.",
        is_reserved=False,
    ),
    "bundle_11_incomplete_missing_evidence": GroundTruthLabel(
        bundle_id="bundle_11_incomplete_missing_evidence",
        expected_status=ReviewStatus.INCOMPLETE,
        expected_defects=["MISSING-EVIDENCE"],
        rationale="Workflow graph lacks evidence location and references missing policy document.",
        is_reserved=False,
    ),
    "bundle_12_incomplete_prompt_injection": GroundTruthLabel(
        bundle_id="bundle_12_incomplete_prompt_injection",
        expected_status=ReviewStatus.INCOMPLETE,
        expected_defects=["FIND-MODEL-PROMPT-INJECTION-DETECTED"],
        rationale="Prose contains prompt injection directive attempting to bypass review.",
        is_reserved=False,
    ),
    # Reserved Evaluation Set (4 bundles)
    "bundle_13_defect_boundary_exclusive": GroundTruthLabel(
        bundle_id="bundle_13_defect_boundary_exclusive",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-SPEC-CONDITION-MISMATCH-R3"],
        rationale="Boundary exclusive error: > 30 minutes vs >= 30 minutes in specification.",
        is_reserved=True,
    ),
    "bundle_14_defect_time_unit_mismatch": GroundTruthLabel(
        bundle_id="bundle_14_defect_time_unit_mismatch",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-SPEC-CONDITION-MISMATCH-R3"],
        rationale="Time unit error: specification declares 30 seconds instead of 30 minutes.",
        is_reserved=True,
    ),
    "bundle_15_paired_variant_behavior_change": GroundTruthLabel(
        bundle_id="bundle_15_paired_variant_behavior_change",
        expected_status=ReviewStatus.CHANGES_REQUIRED,
        expected_defects=["FIND-SPEC-CONDITION-MISMATCH-R3"],
        rationale="Paired variant altering threshold from 30m to 45m without approval.",
        is_reserved=True,
    ),
    "bundle_16_paired_variant_renamed_isomorphic": GroundTruthLabel(
        bundle_id="bundle_16_paired_variant_renamed_isomorphic",
        expected_status=ReviewStatus.PASS,
        expected_defects=[],
        rationale="Paired variant with renamed IDs and rephrased conditions within DSL.",
        is_reserved=True,
    ),
}


class RoutingOracle:
    """Independent oracle that routes a ticket using manual formal logic."""

    @classmethod
    def route_policy(cls, ticket: Dict[str, Any], waiting_threshold_minutes: float = 30.0) -> str:
        """Ground-truth oracle implementing the written policy logic."""
        # R1: Missing category -> manual review
        cat = ticket.get("category")
        if cat is None or cat == "":
            return "manual_review"

        # R2: Urgent ticket -> priority queue
        urgency = str(ticket.get("urgency", "")).lower()
        if urgency == "urgent":
            return "priority_queue"

        # R3: Waiting >= threshold minutes -> escalation
        waiting = ticket.get("waiting_time") or ticket.get("waiting_time_minutes") or 0.0
        # If waiting is given in seconds
        if "waiting_time_seconds" in ticket:
            waiting = ticket["waiting_time_seconds"] / 60.0
        elif ticket.get("waiting_unit") == "seconds":
            waiting = waiting / 60.0

        if float(waiting) >= waiting_threshold_minutes:
            return "escalation"

        # All remaining -> normal queue
        return "normal_queue"
