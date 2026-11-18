"""Validation of model outputs: JSON integrity, hallucinated rule detection, and security constraints."""

from __future__ import annotations
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from reviewer.contracts.models import (
    EvidenceLocation,
    ReviewFinding,
    ReviewStatus,
    Severity,
)
from reviewer.model.client import ModelResponse


class ModelResponseValidator:
    """Validates raw model responses for format, schema, factual grounding, and security constraints."""

    @classmethod
    def validate_and_parse(
        cls,
        response: ModelResponse,
        known_rule_ids: Set[str],
    ) -> Tuple[bool, Optional[Dict[str, Any]], List[ReviewFinding]]:
        """Parses model output and validates factual grounding against known rules.

        Returns (is_valid, parsed_payload, audit_findings).
        """
        audit_findings: List[ReviewFinding] = []

        if response.is_error:
            audit_findings.append(
                ReviewFinding(
                    finding_id="FIND-MODEL-CALL-ERROR",
                    severity=Severity.WARNING,
                    artifact="model",
                    location=EvidenceLocation(file_path="model_client"),
                    evidence=response.error_message or "Model client execution failed",
                    message=f"Model client error: {response.error_message}",
                    correction="Investigate model connectivity or timeout settings.",
                )
            )
            return False, None, audit_findings

        # 1. Parse JSON
        raw_text = response.content.strip()
        # Strip potential markdown code fences if wrapped in ```json ... ```
        if raw_text.startswith("```"):
            m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", raw_text)
            if m:
                raw_text = m.group(1).strip()

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as e:
            audit_findings.append(
                ReviewFinding(
                    finding_id="FIND-MODEL-MALFORMED-JSON",
                    severity=Severity.WARNING,
                    artifact="model",
                    location=EvidenceLocation(file_path="model_response"),
                    evidence=response.content[:150],
                    message=f"Model returned malformed or truncated JSON: {e}",
                    correction="Ensure model generation parameters enforce valid JSON output.",
                )
            )
            return False, None, audit_findings

        if not isinstance(payload, dict):
            audit_findings.append(
                ReviewFinding(
                    finding_id="FIND-MODEL-INVALID-SCHEMA",
                    severity=Severity.WARNING,
                    artifact="model",
                    location=EvidenceLocation(file_path="model_response"),
                    evidence=str(payload)[:100],
                    message="Model response root must be a JSON object",
                    correction="Enforce dictionary output in model prompt.",
                )
            )
            return False, None, audit_findings

        # 2. Schema check
        status_str = payload.get("status")
        if status_str not in ("PASS", "CHANGES_REQUIRED", "INCOMPLETE"):
            audit_findings.append(
                ReviewFinding(
                    finding_id="FIND-MODEL-UNKNOWN-STATUS",
                    severity=Severity.WARNING,
                    artifact="model",
                    location=EvidenceLocation(file_path="model_response"),
                    evidence=f"status: '{status_str}'",
                    message=f"Model returned invalid status: '{status_str}'",
                    correction="Map status to PASS, CHANGES_REQUIRED, or INCOMPLETE.",
                )
            )
            payload["status"] = "INCOMPLETE"

        # 3. Prompt injection check in model summary
        summary = str(payload.get("summary", ""))
        if "OVERRIDDEN BY" in summary.upper() or "IGNORE ALL" in summary.upper():
            audit_findings.append(
                ReviewFinding(
                    finding_id="FIND-MODEL-PROMPT-INJECTION-DETECTED",
                    severity=Severity.BLOCKER,
                    artifact="model",
                    location=EvidenceLocation(file_path="model_response"),
                    evidence=summary[:150],
                    message="Model appears compromised by untrusted prompt injection directives inside input text",
                    correction="Sanitize input text and enforce system instruction primacy.",
                )
            )
            payload["status"] = "CHANGES_REQUIRED"

        # 4. Check findings for hallucinated rule IDs
        raw_findings = payload.get("findings", [])
        validated_findings = []
        for idx, f in enumerate(raw_findings):
            if not isinstance(f, dict):
                continue
            r_ref = f.get("rule_ref")
            if r_ref and r_ref not in known_rule_ids:
                # Hallucinated rule reference!
                audit_findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-MODEL-HALLUCINATED-REF-{idx}",
                        severity=Severity.WARNING,
                        rule_ref=r_ref,
                        artifact="model",
                        location=EvidenceLocation(file_path="model_response"),
                        evidence=f"Cited non-existent rule reference: '{r_ref}'",
                        message=f"Model hallucinated non-existent rule reference '{r_ref}'. Finding discarded.",
                        correction="Ensure model only references known source or spec rule IDs.",
                    )
                )
                continue  # Discard hallucinated finding

            # Consistency check
            sev = f.get("severity", "INFO")
            if sev == "BLOCKER" and payload.get("status") == "PASS":
                # Conflicting claims!
                audit_findings.append(
                    ReviewFinding(
                        finding_id="FIND-MODEL-CONFLICTING-CLAIM",
                        severity=Severity.WARNING,
                        rule_ref=r_ref,
                        artifact="model",
                        location=EvidenceLocation(file_path="model_response"),
                        evidence=f"Status is PASS but finding {f.get('finding_id')} has BLOCKER severity",
                        message="Model claimed status 'PASS' while reporting BLOCKER findings. Automatically correcting status.",
                        correction="Rectify status to CHANGES_REQUIRED when blocker findings exist.",
                    )
                )
                payload["status"] = "CHANGES_REQUIRED"

            validated_findings.append(f)

        payload["findings"] = validated_findings
        return True, payload, audit_findings
