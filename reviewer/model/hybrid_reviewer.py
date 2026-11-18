"""Hybrid AI-assisted reviewer combining deterministic checks with model judgment and failure resilience."""

from __future__ import annotations
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Set

from reviewer.contracts.models import (
    CoverageReport,
    EvidenceLocation,
    ReviewBundle,
    ReviewFinding,
    ReviewReport,
    ReviewStatus,
    Severity,
)
from reviewer.model.client import FakeModelClient, ModelClient, ModelConfig, ModelResponse
from reviewer.model.prompts import build_reviewer_prompt
from reviewer.model.validator import ModelResponseValidator
from reviewer.semantic.reviewer import DeterministicReviewer


class HybridReviewer:
    """Combines deterministic formal checks with validated model intelligence."""

    def __init__(self, model_client: Optional[ModelClient] = None, config: Optional[ModelConfig] = None):
        self.model_client = model_client or FakeModelClient(mode="NORMAL")
        self.config = config or ModelConfig()
        self._report_cache: Dict[str, ReviewReport] = {}

    def review(self, bundle: ReviewBundle, use_cache: bool = True) -> ReviewReport:
        # 1. Compute Cache Key across artifacts, approvals, and model config
        cache_key = self._compute_cache_key(bundle)
        if use_cache and cache_key in self._report_cache:
            cached_report = self._report_cache[cache_key]
            # Stamp cache hit in provenance
            prov = dict(cached_report.provenance)
            prov["cache_hit"] = True
            return cached_report.model_copy(update={"provenance": prov})

        # 2. Run Deterministic Formal Review (Ground Truth Baseline)
        det_report = DeterministicReviewer.review(bundle)

        # 3. Known Rule IDs for Hallucination Protection
        known_rule_ids = {r.id for r in bundle.source_requirements} | {
            r.rule_id for r in bundle.specification.rules
        }

        # 4. Model Call with Bounded Retries & Provenance Tracking
        prompt = build_reviewer_prompt(bundle, det_report)
        response, attempts, retries_used = self._call_model_with_retries(prompt, known_rule_ids)

        # 5. Validate Model Output
        is_valid, model_payload, audit_findings = ModelResponseValidator.validate_and_parse(
            response, known_rule_ids
        )

        # 6. Merge Findings and Enforce Safety Invariants
        merged_findings = list(det_report.findings) + audit_findings

        # If model returned valid supplementary findings
        if is_valid and model_payload:
            for mf in model_payload.get("findings", []):
                # Ensure it's not a duplicate of an existing deterministic finding
                f_id = mf.get("finding_id", "FIND-MODEL-EXTRA")
                if not any(df.finding_id == f_id for df in det_report.findings):
                    merged_findings.append(
                        ReviewFinding(
                            finding_id=f_id,
                            severity=Severity(mf.get("severity", "INFO")),
                            rule_ref=mf.get("rule_ref"),
                            artifact=mf.get("artifact", "model"),
                            location=EvidenceLocation(file_path="model_analysis"),
                            evidence=mf.get("evidence", "Model natural language observation"),
                            message=mf.get("message", "Model identified potential concern"),
                            correction=mf.get("correction"),
                        )
                    )

        # 7. Final Verdict Determination
        # Invariant 1: A deterministic blocker CANNOT be converted into PASS by model.
        det_has_blocker = any(f.severity == Severity.BLOCKER for f in det_report.findings)
        model_has_blocker = any(f.severity == Severity.BLOCKER for f in merged_findings)

        if det_has_blocker or model_has_blocker:
            final_status = ReviewStatus.CHANGES_REQUIRED
            summary = (
                f"CHANGES_REQUIRED: Evidenced blocker exists. "
                f"Deterministic checks found {sum(1 for f in det_report.findings if f.severity == Severity.BLOCKER)} blocker(s). "
                f"Model audit reported {len(audit_findings)} validation observation(s)."
            )
        elif det_report.status == ReviewStatus.INCOMPLETE:
            final_status = ReviewStatus.INCOMPLETE
            summary = det_report.summary
        elif not is_valid and response.is_error:
            # Model execution failed and no blocker was established -> INCOMPLETE
            final_status = ReviewStatus.INCOMPLETE
            summary = f"Review INCOMPLETE: Model evaluation failed ({response.error_message}) and complete certification was prevented."
        else:
            final_status = ReviewStatus.PASS
            obs = model_payload.get("natural_language_observations", []) if model_payload else []
            obs_str = " " + " ".join(obs) if obs else ""
            summary = f"PASS: All effective requirements formally verified across artifacts.{obs_str}"

        # 8. Provenance & Resource Accounting
        p_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        bundle_hash = det_report.provenance.get("bundle_hash", "")

        check_results = dict(det_report.check_results)
        check_results["model_validation"] = {
            "is_valid": is_valid,
            "attempts": attempts,
            "retries_used": retries_used,
            "latency_ms": response.latency_ms,
            "token_usage": response.token_usage,
            "audit_findings_count": len(audit_findings),
        }

        final_report = ReviewReport(
            status=final_status,
            summary=summary,
            findings=merged_findings,
            coverage=det_report.coverage,
            check_results=check_results,
            provenance={
                "bundle_id": bundle.bundle_id,
                "bundle_hash": bundle_hash,
                "cache_key": cache_key,
                "model_name": response.model_name,
                "model_temperature": response.temperature,
                "prompt_hash": p_hash,
                "response_timestamp": response.timestamp,
                "latency_ms": response.latency_ms,
                "token_usage": response.token_usage,
                "retries": retries_used,
                "cache_hit": False,
            },
        )

        if use_cache:
            self._report_cache[cache_key] = final_report

        return final_report

    def _call_model_with_retries(
        self, prompt: str, known_rule_ids: Set[str]
    ) -> Tuple[ModelResponse, int, int]:
        """Calls model with bounded retries upon timeout or malformed JSON."""
        max_attempts = 1 + self.config.max_retries
        attempts = 0
        retries_used = 0
        last_response: Optional[ModelResponse] = None

        while attempts < max_attempts:
            attempts += 1
            last_response = self.model_client.generate(prompt, self.config)

            if last_response.is_error:
                retries_used += 1
                continue

            # Quick check if JSON is parseable
            try:
                raw_text = last_response.content.strip()
                if raw_text.startswith("```"):
                    raw_text = raw_text.strip("`").removeprefix("json").strip()
                json.loads(raw_text)
                return last_response, attempts, retries_used
            except Exception:
                retries_used += 1
                continue

        return last_response or ModelResponse(
            content="",
            raw_response={},
            prompt_hash="",
            model_name=self.config.model_name,
            temperature=self.config.temperature,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            latency_ms=0.0,
            token_usage={"prompt_tokens": 0, "completion_tokens": 0},
            is_error=True,
            error_message="Exhausted bounded retries",
        ), attempts, retries_used

    def _compute_cache_key(self, bundle: ReviewBundle) -> str:
        """Computes deterministic cache key from input bundle, approvals, and config."""
        bundle_repr = bundle.model_dump_json()
        cfg_repr = f"{self.config.model_name}:{self.config.temperature}:{self.config.max_tokens}"
        raw = f"{bundle_repr}|{cfg_repr}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
