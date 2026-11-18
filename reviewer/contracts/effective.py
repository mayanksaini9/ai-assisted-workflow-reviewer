"""Derivation of effective requirements from anchored sources and trusted approval records."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from reviewer.contracts.conditions import (
    ComparisonCondition,
    ConditionNode,
    Duration,
    TrueCondition,
)
from reviewer.contracts.models import (
    ApprovalRecord,
    EvidenceLocation,
    ReviewBundle,
    ReviewFinding,
    RuleException,
    Severity,
    SourceRequirement,
)
from reviewer.contracts.parser import DSLParser, parse_condition_dict


@dataclass
class EffectiveRequirement:
    """The authoritative effective requirement after applying verified approvals."""
    rule_id: str
    original_text: str
    condition: ConditionNode
    action: str
    precedence: int
    source_anchor: EvidenceLocation
    applied_changes: List[str] = field(default_factory=list)
    exceptions: List[RuleException] = field(default_factory=list)
    is_supported: bool = True
    support_notes: Optional[str] = None


class EffectiveRequirementsResolver:
    """Resolves and validates effective requirements across revisions and approvals."""

    @classmethod
    def resolve(
        cls, bundle: ReviewBundle
    ) -> Tuple[List[EffectiveRequirement], List[ReviewFinding]]:
        findings: List[ReviewFinding] = []
        effective_map: Dict[str, EffectiveRequirement] = {}

        # 1. Parse base source requirements
        for src in bundle.source_requirements:
            cond: ConditionNode
            if src.condition:
                cond = parse_condition_dict(src.condition)
            elif src.condition_text:
                cond = DSLParser.parse(src.condition_text)
            else:
                cond = DSLParser.parse(src.text)

            is_supported = not (cond.to_dict().get("type") == "Unsupported")
            support_notes = (
                cond.to_dict().get("reason") if not is_supported else None
            )

            # Check for suspicious prose claims of approval inside the requirement text
            cls._check_prose_approval_claims(src, bundle.approval_records, findings)
            # Check for adversarial prompt injection attempts
            cls._check_prompt_injection(src, findings)

            effective_map[src.id] = EffectiveRequirement(
                rule_id=src.id,
                original_text=src.text,
                condition=cond,
                action=src.action,
                precedence=src.precedence,
                source_anchor=src.anchor,
                applied_changes=[],
                exceptions=list(src.exceptions),
                is_supported=is_supported,
                support_notes=support_notes,
            )

        # 2. Check approval records for contradictions & validity
        approved_by_rule_field: Dict[Tuple[str, str], List[ApprovalRecord]] = {}

        for rec in bundle.approval_records:
            if not rec.is_top_level:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-APPR-UNTRUSTED-{rec.change_id}",
                        severity=Severity.BLOCKER,
                        rule_ref=rec.rule_ref,
                        artifact="approval",
                        version=rec.version,
                        location=EvidenceLocation(file_path="approvals.json", section=rec.change_id),
                        evidence=f"Approval claim {rec.change_id} is not marked top-level trusted metadata",
                        message=f"Change {rec.change_id} on rule {rec.rule_ref} is not an authorized top-level approval record",
                        correction="Ensure changes are formally authorized via top-level signed approval records",
                    )
                )
                continue

            if rec.status == "APPROVED":
                key = (rec.rule_ref, rec.field)
                approved_by_rule_field.setdefault(key, []).append(rec)

        # 3. Detect contradictions among approved records
        valid_approved_records: List[ApprovalRecord] = []
        for (r_id, f_field), recs in approved_by_rule_field.items():
            if len(recs) > 1:
                # Check if they have contradictory values without clear chronological succession
                unique_values = {str(r.new_value) for r in recs}
                if len(unique_values) > 1:
                    # Contradictory approvals detected!
                    findings.append(
                        ReviewFinding(
                            finding_id=f"FIND-APPR-CONFLICT-{r_id}-{f_field}",
                            severity=Severity.BLOCKER,
                            rule_ref=r_id,
                            artifact="approval",
                            version=recs[0].version,
                            location=EvidenceLocation(file_path="approvals.json", section=r_id),
                            evidence=f"Conflicting approved changes for {r_id}.{f_field}: " + ", ".join(
                                f"{r.change_id} (value={r.new_value}, ts={r.timestamp})" for r in recs
                            ),
                            message=f"Contradictory approval records detected for rule {r_id} field '{f_field}'. Reviewer cannot silently choose a convenient version.",
                            correction="Resolve conflicting approval records to establish a single authoritative policy revision.",
                        )
                    )
                    # Do not apply conflicting records
                    continue
                else:
                    # Identical approved values, sort and take latest
                    recs.sort(key=lambda r: (r.timestamp, r.version))
                    valid_approved_records.append(recs[-1])
            else:
                valid_approved_records.append(recs[0])

        # 4. Apply valid approved changes in chronological order
        valid_approved_records.sort(key=lambda r: (r.timestamp, r.version))

        for rec in valid_approved_records:
            if rec.rule_ref not in effective_map:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-APPR-ORPHAN-{rec.change_id}",
                        severity=Severity.WARNING,
                        rule_ref=rec.rule_ref,
                        artifact="approval",
                        version=rec.version,
                        location=EvidenceLocation(file_path="approvals.json", section=rec.change_id),
                        evidence=f"Approved change {rec.change_id} references non-existent rule {rec.rule_ref}",
                        message=f"Approval record {rec.change_id} targets non-existent rule reference '{rec.rule_ref}'",
                        correction="Verify whether the target rule ID was renamed or deleted.",
                    )
                )
                continue

            eff = effective_map[rec.rule_ref]
            cls._apply_change(eff, rec)
            eff.applied_changes.append(rec.change_id)

        # Sort effective requirements by precedence
        effective_list = sorted(effective_map.values(), key=lambda r: r.precedence)
        return effective_list, findings

    @classmethod
    def _apply_change(cls, eff: EffectiveRequirement, rec: ApprovalRecord) -> None:
        """Applies an approved change record to an effective requirement."""
        field_name = rec.field.lower()
        new_val = rec.new_value

        if field_name == "action":
            eff.action = str(new_val)
        elif field_name == "precedence":
            eff.precedence = int(new_val)
        elif field_name == "threshold":
            # If the current condition is ComparisonCondition, update its threshold
            cur_cond = eff.condition
            if isinstance(cur_cond, ComparisonCondition):
                # Check if new_val is duration dict or scalar
                if isinstance(new_val, dict) and new_val.get("unit"):
                    dur = Duration(float(new_val["value"]), new_val["unit"])
                    eff.condition = ComparisonCondition(
                        field_name=cur_cond.field_name,
                        operator=cur_cond.operator,
                        value=dur,
                    )
                elif isinstance(new_val, (int, float)):
                    if isinstance(cur_cond.value, Duration):
                        eff.condition = ComparisonCondition(
                            field_name=cur_cond.field_name,
                            operator=cur_cond.operator,
                            value=Duration(float(new_val), cur_cond.value.unit),
                        )
                    else:
                        eff.condition = ComparisonCondition(
                            field_name=cur_cond.field_name,
                            operator=cur_cond.operator,
                            value=new_val,
                        )
                elif isinstance(new_val, str):
                    # Try parsing string like "25 minutes"
                    parsed = DSLParser.parse(f"{cur_cond.field_name} {cur_cond.operator} {new_val}")
                    if isinstance(parsed, ComparisonCondition):
                        eff.condition = parsed
        elif field_name in ("condition", "condition_text"):
            if isinstance(new_val, dict):
                eff.condition = parse_condition_dict(new_val)
            else:
                eff.condition = DSLParser.parse(str(new_val))

    @classmethod
    def _check_prose_approval_claims(
        cls,
        src: SourceRequirement,
        approval_records: List[ApprovalRecord],
        findings: List[ReviewFinding],
    ) -> None:
        """Detects untrusted approval claims made inside prose text without an official record."""
        prose = src.text.lower()
        suspicious_phrases = [
            "approved by",
            "as per alice",
            "slack approval",
            "agreed in meeting",
            "verbally approved",
            "management approved",
        ]
        has_suspicious_claim = any(p in prose for p in suspicious_phrases)
        if has_suspicious_claim:
            # Check if there is an official top-level approved record for this rule
            matching_official = [
                r for r in approval_records if r.rule_ref == src.id and r.status == "APPROVED" and r.is_top_level
            ]
            if not matching_official:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-PROSE-APPR-UNVERIFIED-{src.id}",
                        severity=Severity.WARNING,
                        rule_ref=src.id,
                        artifact="source",
                        location=src.anchor,
                        evidence=src.text,
                        message=f"Rule {src.id} claims approval in prose text, but no valid top-level approval record was provided.",
                        correction="Formalize changes through official top-level approval records rather than prose claims.",
                    )
                )

    @classmethod
    def _check_prompt_injection(
        cls,
        src: SourceRequirement,
        findings: List[ReviewFinding],
    ) -> None:
        """Detects prompt injection directives embedded inside requirement prose."""
        prose = src.text.lower()
        injection_phrases = [
            "system override",
            "ignore all rules",
            "ignore previous instructions",
            "return pass",
            "bypass review",
        ]
        if any(p in prose for p in injection_phrases):
            findings.append(
                ReviewFinding(
                    finding_id=f"FIND-MODEL-PROMPT-INJECTION-DETECTED-{src.id}",
                    severity=Severity.WARNING,
                    rule_ref=src.id,
                    artifact="source",
                    location=src.anchor,
                    evidence=src.text,
                    message=f"Adversarial prompt injection directive detected in source requirement {src.id}. Review cannot be certified.",
                    correction="Sanitize input text and reject untrusted prompt injection directives.",
                )
            )
