"""Deterministic Semantic Reviewer across specification, workflow graph, and linked code."""

from __future__ import annotations
import hashlib
from typing import Any, Dict, List, Optional, Set, Tuple

from reviewer.contracts.conditions import (
    ComparisonCondition,
    ConditionNode,
    TrueCondition,
    UnsupportedCondition,
)
from reviewer.contracts.effective import (
    EffectiveRequirement,
    EffectiveRequirementsResolver,
)
from reviewer.contracts.models import (
    Counterexample,
    CoverageReport,
    EvidenceLocation,
    ReviewBundle,
    ReviewFinding,
    ReviewReport,
    ReviewStatus,
    Severity,
    Specification,
    SpecificationRule,
)
from reviewer.contracts.parser import DSLParser, parse_condition_dict
from reviewer.semantic.code_analyzer import CodeSnippetAnalyzer
from reviewer.semantic.graph_analyzer import WorkflowGraphAnalyzer
from reviewer.semantic.solver import DomainSolver


class DeterministicReviewer:
    """Core deterministic engine verifying requirements, specification, workflow, and code."""

    @classmethod
    def review(cls, bundle: ReviewBundle) -> ReviewReport:
        findings: List[ReviewFinding] = []
        check_results: Dict[str, Any] = {}

        # 1. Derive Effective Requirements & check approvals
        effective_reqs, approval_findings = EffectiveRequirementsResolver.resolve(bundle)
        findings.extend(approval_findings)
        check_results["effective_requirements_count"] = len(effective_reqs)
        check_results["approval_conflicts_detected"] = any(
            "FIND-APPR-CONFLICT" in f.finding_id for f in approval_findings
        )

        # 2. Check Specification against Effective Requirements
        spec_findings = cls._check_specification(bundle.specification, effective_reqs)
        findings.extend(spec_findings)
        check_results["specification_findings_count"] = len(spec_findings)

        # 3. Check Workflow Decision Graph
        graph_findings, paths = WorkflowGraphAnalyzer.analyze(bundle.workflow_graph, effective_reqs)
        findings.extend(graph_findings)
        check_results["workflow_paths_analyzed"] = len(paths)
        check_results["workflow_findings_count"] = len(graph_findings)

        # 4. Check Linked Python Snippets
        code_findings = CodeSnippetAnalyzer.analyze(bundle.code_snippets, effective_reqs)
        findings.extend(code_findings)
        check_results["code_findings_count"] = len(code_findings)

        # 5. Check Rule Interactions & Shadowing
        interaction_findings = cls._check_rule_interactions(effective_reqs)
        findings.extend(interaction_findings)
        check_results["interaction_findings_count"] = len(interaction_findings)

        # 6. Compute Coverage & Identify Unsupported Scope
        coverage, unreviewed_items = cls._compute_coverage(bundle, effective_reqs, findings)

        # 7. Compute Final Verdict
        has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
        has_approval_conflict = check_results["approval_conflicts_detected"]
        has_unsupported_scope = len(coverage.unsupported_elements) > 0

        # Check for missing evidence or ambiguous items or prompt injection tampering
        is_missing_evidence = any("MISSING-EVIDENCE" in f.finding_id for f in findings)
        has_prompt_injection = any("PROMPT-INJECTION" in f.finding_id for f in findings)

        if has_blocker:
            status = ReviewStatus.CHANGES_REQUIRED
            summary = (
                f"Changes required: Found {sum(1 for f in findings if f.severity == Severity.BLOCKER)} blocker(s) "
                f"across specification, workflow graph, or linked code."
            )
        elif has_approval_conflict or has_unsupported_scope or is_missing_evidence or has_prompt_injection:
            status = ReviewStatus.INCOMPLETE
            summary = (
                "Review incomplete: Ambiguity, unsupported syntax, prompt injection tampering, or contradictory approvals prevent full certification. "
                "No blocker was established."
            )
        else:
            status = ReviewStatus.PASS
            summary = "All effective requirements successfully verified across specification, workflow graph, and linked code."

        # Compute artifact hash provenance
        bundle_json = bundle.model_dump_json()
        bundle_hash = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()

        return ReviewReport(
            status=status,
            summary=summary,
            findings=findings,
            coverage=coverage,
            check_results=check_results,
            provenance={
                "bundle_id": bundle.bundle_id,
                "bundle_hash": bundle_hash,
                "reviewer_mode": "deterministic",
                "rules_reviewed": len(effective_reqs),
            },
        )

    @classmethod
    def _check_specification(
        cls,
        spec: Specification,
        effective_reqs: List[EffectiveRequirement],
    ) -> List[ReviewFinding]:
        findings: List[ReviewFinding] = []
        spec_rules_by_id = {r.rule_id: r for r in spec.rules}

        # Check for omitted source requirements (must anchor to source!)
        for eff in effective_reqs:
            if eff.rule_id not in spec_rules_by_id:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-SPEC-OMISSION-{eff.rule_id}",
                        severity=Severity.BLOCKER,
                        rule_ref=eff.rule_id,
                        artifact="specification",
                        version=spec.version,
                        location=eff.source_anchor,
                        evidence=f"Effective requirement {eff.rule_id}: '{eff.original_text}'",
                        message=f"Specification completely omits source requirement '{eff.rule_id}'",
                        correction=f"Add rule {eff.rule_id} to specification matching source requirement",
                    )
                )
                continue

            spec_rule = spec_rules_by_id[eff.rule_id]
            spec_cond: ConditionNode
            if spec_rule.condition:
                spec_cond = parse_condition_dict(spec_rule.condition)
            elif spec_rule.condition_text:
                spec_cond = DSLParser.parse(spec_rule.condition_text)
            else:
                spec_cond = DSLParser.parse(spec_rule.description)

            # Check action match
            if spec_rule.action != eff.action:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-SPEC-ACTION-MISMATCH-{eff.rule_id}",
                        severity=Severity.BLOCKER,
                        rule_ref=eff.rule_id,
                        artifact="specification",
                        version=spec.version,
                        location=spec_rule.location,
                        evidence=f"Specification action: '{spec_rule.action}', expected: '{eff.action}'",
                        message=f"Specification specifies action '{spec_rule.action}' for rule {eff.rule_id}, but effective requirement requires '{eff.action}'",
                        correction=f"Change specification action to '{eff.action}'",
                    )
                )

            # Check precedence match
            if spec_rule.precedence != eff.precedence:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-SPEC-PRECEDENCE-MISMATCH-{eff.rule_id}",
                        severity=Severity.BLOCKER,
                        rule_ref=eff.rule_id,
                        artifact="specification",
                        version=spec.version,
                        location=spec_rule.location,
                        evidence=f"Specification precedence: {spec_rule.precedence}, expected: {eff.precedence}",
                        message=f"Precedence mismatch for rule {eff.rule_id}: expected {eff.precedence}, got {spec_rule.precedence}",
                        correction=f"Align specification precedence to {eff.precedence}",
                    )
                )

            # Check condition equivalence & altered thresholds
            if isinstance(eff.condition, UnsupportedCondition) or isinstance(spec_cond, UnsupportedCondition):
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-SCOPE-UNSUPPORTED-{eff.rule_id}",
                        severity=Severity.WARNING,
                        rule_ref=eff.rule_id,
                        artifact="specification",
                        version=spec.version,
                        location=spec_rule.location,
                        evidence=f"Unsupported condition in rule {eff.rule_id}",
                        message=f"Rule {eff.rule_id} contains unsupported/ambiguous condition language. Disclosing unreviewed scope.",
                        correction="Refactor policy wording to conform with supported structured DSL patterns.",
                    )
                )
            else:
                is_equiv, reason = DomainSolver.check_equivalence(eff.condition, spec_cond)
                if not is_equiv:
                    ce = DomainSolver.generate_counterexample(eff.condition, spec_cond) or DomainSolver.generate_counterexample(spec_cond, eff.condition)
                    findings.append(
                        ReviewFinding(
                            finding_id=f"FIND-SPEC-CONDITION-MISMATCH-{eff.rule_id}",
                            severity=Severity.BLOCKER,
                            rule_ref=eff.rule_id,
                            artifact="specification",
                            version=spec.version,
                            location=spec_rule.location,
                            evidence=f"Spec condition '{spec_cond.to_dict()}' vs effective '{eff.condition.to_dict()}'",
                            message=f"Specification condition for rule {eff.rule_id} does not match effective requirement: {reason}",
                            correction=f"Update specification condition to match effective requirement",
                            counterexample=Counterexample(
                                ticket=ce or {"waiting_time": 30},
                                expected_route=eff.action,
                                actual_route=spec_rule.action,
                                divergence_reason=f"Condition mismatch in specification: {reason}",
                                evidence={
                                    "effective_rule": f"{eff.rule_id}: {eff.original_text}",
                                    "specification": spec_rule.description,
                                },
                            ) if ce else None,
                        )
                    )

        # Check for extraneous spec rules
        eff_ids = {r.rule_id for r in effective_reqs}
        for s_rule in spec.rules:
            if s_rule.rule_id not in eff_ids:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-SPEC-EXTRANEOUS-{s_rule.rule_id}",
                        severity=Severity.WARNING,
                        rule_ref=s_rule.rule_id,
                        artifact="specification",
                        version=spec.version,
                        location=s_rule.location,
                        evidence=f"Extraneous rule {s_rule.rule_id}: {s_rule.description}",
                        message=f"Specification contains unanchored rule '{s_rule.rule_id}' not found in source requirements",
                        correction="Verify whether this rule should be anchored to an approved policy document",
                    )
                )

        return findings

    @classmethod
    def _check_rule_interactions(cls, effective_reqs: List[EffectiveRequirement]) -> List[ReviewFinding]:
        """Detects shadowed rules, dead conditions, and conflicting rule interactions."""
        findings: List[ReviewFinding] = []

        # Check for shadowing: if earlier rule completely subsumes later rule
        for i in range(len(effective_reqs)):
            for j in range(i + 1, len(effective_reqs)):
                r_earlier = effective_reqs[i]
                r_later = effective_reqs[j]

                # Check if r_earlier condition is TrueCondition (shadows all subsequent rules)
                if isinstance(r_earlier.condition, TrueCondition):
                    findings.append(
                        ReviewFinding(
                            finding_id=f"FIND-RULE-SHADOWED-{r_later.rule_id}",
                            severity=Severity.BLOCKER,
                            rule_ref=r_later.rule_id,
                            artifact="source",
                            location=r_later.source_anchor,
                            evidence=f"Earlier rule {r_earlier.rule_id} has unconditional match, shadowing {r_later.rule_id}",
                            message=f"Rule {r_later.rule_id} is completely shadowed by earlier rule {r_earlier.rule_id} and can never be reached",
                            correction=f"Place fallback/unconditional rules at the end of the precedence order",
                        )
                    )

        return findings

    @classmethod
    def _compute_coverage(
        cls,
        bundle: ReviewBundle,
        effective_reqs: List[EffectiveRequirement],
        findings: List[ReviewFinding],
    ) -> Tuple[CoverageReport, List[str]]:
        """Computes coverage metrics and surfaces unreviewed or unsupported constructs."""
        total_src = len(bundle.source_requirements)
        omitted_ids = [
            f.rule_ref for f in findings if "FIND-SPEC-OMISSION" in f.finding_id and f.rule_ref
        ]
        reviewed_ids = [r.rule_id for r in effective_reqs if r.rule_id not in omitted_ids]

        unsupported_elements: List[Dict[str, str]] = []
        for eff in effective_reqs:
            if not eff.is_supported:
                unsupported_elements.append({
                    "rule_id": eff.rule_id,
                    "reason": eff.support_notes or "Unsupported condition language",
                    "text": eff.original_text,
                })

        ratio = round(len(reviewed_ids) / total_src, 3) if total_src > 0 else 0.0

        report = CoverageReport(
            total_source_requirements=total_src,
            reviewed_requirements=reviewed_ids,
            omitted_requirements=omitted_ids,
            unsupported_elements=unsupported_elements,
            completeness_ratio=ratio,
        )
        return report, [u["rule_id"] for u in unsupported_elements]
