"""Initial working checkpoint (Baseline Reviewer v0) for comparative evaluation.

The baseline reviewer performs naive syntactic ID and string matching:
- Matches rule IDs verbatim
- Compares condition strings literally without interval or time unit normalization
- Naively accepts docstrings/metadata of Python snippets without AST analysis
- Takes the first approved change record without checking for contradictions
- Cannot synthesize concrete ticket counterexamples
"""

from __future__ import annotations
import hashlib
from typing import Any, Dict, List, Optional

from reviewer.contracts.models import (
    CoverageReport,
    EvidenceLocation,
    ReviewBundle,
    ReviewFinding,
    ReviewReport,
    ReviewStatus,
    Severity,
)


class BaselineReviewer:
    """Initial checkpoint reviewer (v0) relying on syntactic matching."""

    @classmethod
    def review(cls, bundle: ReviewBundle) -> ReviewReport:
        findings: List[ReviewFinding] = []
        spec_rules_by_id = {r.rule_id: r for r in bundle.specification.rules}

        # 1. Naive approval check (takes first record, ignores conflicts)
        approved_thresholds: Dict[str, Any] = {}
        for app in bundle.approval_records:
            if app.status == "APPROVED" and app.field == "threshold":
                if app.rule_ref not in approved_thresholds:
                    approved_thresholds[app.rule_ref] = app.new_value

        # 2. Check source requirements against spec rules
        for src in bundle.source_requirements:
            if src.id not in spec_rules_by_id:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-BASELINE-OMISSION-{src.id}",
                        severity=Severity.BLOCKER,
                        rule_ref=src.id,
                        artifact="specification",
                        location=src.anchor,
                        evidence=src.text,
                        message=f"Rule {src.id} missing from specification",
                    )
                )
                continue

            spec_rule = spec_rules_by_id[src.id]
            # Naive string comparison of conditions without unit normalization
            src_cond = src.condition_text or src.text
            spec_cond = spec_rule.condition_text or spec_rule.description

            # If an approved threshold was recorded, naive substitution
            if src.id in approved_thresholds:
                target_str = str(approved_thresholds[src.id])
                if target_str not in spec_cond:
                    findings.append(
                        ReviewFinding(
                            finding_id=f"FIND-BASELINE-THRESHOLD-{src.id}",
                            severity=Severity.BLOCKER,
                            rule_ref=src.id,
                            artifact="specification",
                            location=spec_rule.location,
                            evidence=spec_cond,
                            message=f"Threshold in spec does not match approved change {target_str}",
                        )
                    )
            else:
                # Verbatim string equality check
                if src_cond.strip() != spec_cond.strip() and src.action == spec_rule.action:
                    # Naive reviewer flags valid equivalent formulations (e.g. 0.5 hours vs 30 minutes) as blockers!
                    findings.append(
                        ReviewFinding(
                            finding_id=f"FIND-BASELINE-STRING-MISMATCH-{src.id}",
                            severity=Severity.BLOCKER,
                            rule_ref=src.id,
                            artifact="specification",
                            location=spec_rule.location,
                            evidence=f"Spec: '{spec_cond}' != Source: '{src_cond}'",
                            message=f"Condition string mismatch in rule {src.id}",
                        )
                    )

            if src.action != spec_rule.action:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-BASELINE-ACTION-MISMATCH-{src.id}",
                        severity=Severity.BLOCKER,
                        rule_ref=src.id,
                        artifact="specification",
                        location=spec_rule.location,
                        evidence=f"Spec: {spec_rule.action} vs Source: {src.action}",
                        message=f"Action mismatch in rule {src.id}",
                    )
                )

        # 3. Naive workflow check: only checks if node_id references rule_ref
        wf_rule_refs = {n.rule_ref for n in bundle.workflow_graph.nodes.values() if n.rule_ref}
        for src in bundle.source_requirements:
            if src.id not in wf_rule_refs and src.action != "normal_queue":
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-BASELINE-WF-MISSING-{src.id}",
                        severity=Severity.BLOCKER,
                        rule_ref=src.id,
                        artifact="workflow",
                        location=src.anchor,
                        evidence=f"Rule {src.id} not in workflow",
                        message=f"Workflow graph lacks node referencing {src.id}",
                    )
                )

        # 4. Naive code check: only checks if snippet rule_ref exists, NO AST analysis!
        # (This causes false pass on Bundle 08 where code disagrees with docstring!)

        has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
        status = ReviewStatus.CHANGES_REQUIRED if has_blocker else ReviewStatus.PASS

        total_src = len(bundle.source_requirements)
        reviewed_count = total_src - sum(1 for f in findings if "OMISSION" in f.finding_id)
        coverage = CoverageReport(
            total_source_requirements=total_src,
            reviewed_requirements=[s.id for s in bundle.source_requirements],
            omitted_requirements=[],
            unsupported_elements=[],
            completeness_ratio=round(reviewed_count / total_src, 3) if total_src else 0.0,
        )

        bundle_hash = hashlib.sha256(bundle.model_dump_json().encode("utf-8")).hexdigest()
        return ReviewReport(
            status=status,
            summary=f"Baseline Reviewer v0 verdict: {status.value}",
            findings=findings,
            coverage=coverage,
            check_results={"baseline_checks_executed": True},
            provenance={"reviewer": "BaselineReviewer-v0", "bundle_hash": bundle_hash},
        )
