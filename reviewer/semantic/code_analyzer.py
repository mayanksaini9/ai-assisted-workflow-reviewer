"""Analyzer for linked Python code snippets: restricted AST verification and semantic agreement."""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

from reviewer.contracts.conditions import (
    ConditionNode,
    Duration,
    TrueCondition,
    UnsupportedCondition,
)
from reviewer.contracts.effective import EffectiveRequirement
from reviewer.contracts.models import (
    CodeSnippet,
    Counterexample,
    EvidenceLocation,
    ReviewFinding,
    Severity,
)
from reviewer.contracts.parser import RestrictedPythonASTAnalyzer
from reviewer.semantic.solver import DomainSolver


class CodeSnippetAnalyzer:
    """Performs static AST inspection of linked Python snippets against effective requirements."""

    @classmethod
    def analyze(
        cls,
        snippets: List[CodeSnippet],
        effective_requirements: List[EffectiveRequirement],
    ) -> List[ReviewFinding]:
        findings: List[ReviewFinding] = []
        eff_by_id = {r.rule_id: r for r in effective_requirements}

        for snip in snippets:
            # 1. Syntax & Safety validation
            is_safe, error_msg, tree = RestrictedPythonASTAnalyzer.validate_syntax_and_safety(snip.code)
            if not is_safe:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-CODE-UNSAFE-{snip.snippet_id}",
                        severity=Severity.BLOCKER,
                        rule_ref=snip.rule_ref,
                        artifact="code",
                        location=snip.location or EvidenceLocation(file_path="routing.py"),
                        evidence=snip.code.strip()[:100],
                        message=f"Code snippet '{snip.snippet_id}' failed safety/syntax checks: {error_msg}",
                        correction="Refactor code to strictly adhere to the restricted Python subset without arbitrary execution.",
                    )
                )
                continue

            # 2. Extract branches (ConditionNode, return_value)
            branches = RestrictedPythonASTAnalyzer.extract_branches(snip.code)

            # 3. If linked to a specific rule_ref, verify that rule
            if snip.rule_ref:
                target_eff = eff_by_id.get(snip.rule_ref)
                if not target_eff:
                    findings.append(
                        ReviewFinding(
                            finding_id=f"FIND-CODE-ORPHAN-{snip.snippet_id}",
                            severity=Severity.WARNING,
                            rule_ref=snip.rule_ref,
                            artifact="code",
                            location=snip.location or EvidenceLocation(file_path="routing.py"),
                            evidence=f"Linked rule_ref '{snip.rule_ref}'",
                            message=f"Snippet '{snip.snippet_id}' references non-existent rule '{snip.rule_ref}'",
                            correction="Verify rule reference in snippet metadata",
                        )
                    )
                    continue

                cls._verify_snippet_against_rule(snip, branches, target_eff, findings)

            # 4. If snippet is a full routing function, verify overall behavior across all effective rules
            if len(branches) > 1 and not snip.rule_ref:
                cls._verify_full_routing_function(snip, branches, effective_requirements, findings)

        return findings

    @classmethod
    def _verify_snippet_against_rule(
        cls,
        snip: CodeSnippet,
        branches: List[Tuple[ConditionNode, str]],
        eff: EffectiveRequirement,
        findings: List[ReviewFinding],
    ) -> None:
        """Checks if a snippet linked to a specific rule correctly implements that rule."""
        # Find matching branch for the rule action or condition
        matching_branches = [b for b in branches if b[1] == eff.action]
        if not matching_branches:
            # Code does not return the expected action!
            findings.append(
                ReviewFinding(
                    finding_id=f"FIND-CODE-ACTION-MISMATCH-{snip.snippet_id}-{eff.rule_id}",
                    severity=Severity.BLOCKER,
                    rule_ref=eff.rule_id,
                    artifact="code",
                    location=snip.location or EvidenceLocation(file_path="routing.py"),
                    evidence=snip.code.strip()[:120],
                    message=f"Code snippet '{snip.snippet_id}' claims to implement {eff.rule_id} but never returns expected action '{eff.action}'",
                    correction=f"Ensure snippet returns '{eff.action}' for rule {eff.rule_id}",
                )
            )
            return

        code_cond, return_action = matching_branches[0]
        if isinstance(eff.condition, UnsupportedCondition):
            return

        # Check semantic equivalence between code condition and effective condition
        is_equiv, reason = DomainSolver.check_equivalence(code_cond, eff.condition)
        if not is_equiv:
            # Behavioral disagreement in code despite matching metadata!
            ce = DomainSolver.generate_counterexample(eff.condition, code_cond) or DomainSolver.generate_counterexample(code_cond, eff.condition)
            findings.append(
                ReviewFinding(
                    finding_id=f"FIND-CODE-LOGIC-MISMATCH-{snip.snippet_id}-{eff.rule_id}",
                    severity=Severity.BLOCKER,
                    rule_ref=eff.rule_id,
                    artifact="code",
                    location=snip.location or EvidenceLocation(file_path="routing.py"),
                    evidence=snip.code.strip()[:150],
                    message=f"Script disagreement on rule {eff.rule_id} despite matching metadata: {reason}",
                    correction=f"Update Python snippet condition to match effective requirement '{eff.condition.to_dict()}'",
                    counterexample=Counterexample(
                        ticket=ce or {"waiting_time": 30},
                        expected_route=eff.action,
                        actual_route="disagreement",
                        divergence_reason=f"Code condition differs from effective rule: {reason}",
                        evidence={
                            "effective_rule": f"{eff.rule_id}: {eff.original_text}",
                            "code_snippet": snip.code.strip()[:100],
                        },
                    ) if ce else None,
                )
            )

    @classmethod
    def _verify_full_routing_function(
        cls,
        snip: CodeSnippet,
        branches: List[Tuple[ConditionNode, str]],
        effective_requirements: List[EffectiveRequirement],
        findings: List[ReviewFinding],
    ) -> None:
        """Verifies a full routing function against the ordered list of effective requirements."""
        # Check rule precedence and conditions step by step
        eff_index = 0
        for branch_idx, (code_cond, code_action) in enumerate(branches):
            if eff_index >= len(effective_requirements):
                break

            target_eff = effective_requirements[eff_index]
            if isinstance(target_eff.condition, UnsupportedCondition):
                eff_index += 1
                continue

            # If default/fallback
            if isinstance(code_cond, TrueCondition) and target_eff.action == code_action:
                eff_index += 1
                continue

            # Check equivalence
            is_equiv, _ = DomainSolver.check_equivalence(code_cond, target_eff.condition)
            if not is_equiv or code_action != target_eff.action:
                ce = DomainSolver.generate_counterexample(target_eff.condition, code_cond) or {
                    "category": "billing",
                    "urgency": "urgent",
                    "waiting_time": 30,
                }
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-CODE-ORDER-MISMATCH-{snip.snippet_id}-step-{eff_index}",
                        severity=Severity.BLOCKER,
                        rule_ref=target_eff.rule_id,
                        artifact="code",
                        location=snip.location or EvidenceLocation(file_path="routing.py"),
                        evidence=f"Branch {branch_idx}: returns '{code_action}'",
                        message=f"Code branch at position {branch_idx} disagrees with effective requirement {target_eff.rule_id} (expected '{target_eff.action}')",
                        correction=f"Align code order and condition with effective requirement {target_eff.rule_id}",
                        counterexample=Counterexample(
                            ticket=ce,
                            expected_route=target_eff.action,
                            actual_route=code_action,
                            divergence_reason="Code branch condition/action diverges from expected rule sequence",
                            evidence={
                                "expected": f"{target_eff.rule_id} -> {target_eff.action}",
                                "actual_code": f"Branch {branch_idx} -> {code_action}",
                            },
                        ),
                    )
                )
            eff_index += 1
