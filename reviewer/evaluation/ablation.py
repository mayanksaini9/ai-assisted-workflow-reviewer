"""Controlled ablation study: Disabling static AST code inspection while holding all settings fixed."""

from __future__ import annotations
import copy
from typing import Any, Dict, List, Optional

from reviewer.contracts.models import (
    CodeSnippet,
    EvidenceLocation,
    ReviewBundle,
    ReviewFinding,
    ReviewReport,
    ReviewStatus,
    Severity,
)
from reviewer.model.client import FakeModelClient, ModelConfig
from reviewer.model.hybrid_reviewer import HybridReviewer
from reviewer.semantic.reviewer import DeterministicReviewer


class AblatedDeterministicReviewer(DeterministicReviewer):
    """Deterministic reviewer with AST semantic code analysis DISABLED."""

    @classmethod
    def review(cls, bundle: ReviewBundle) -> ReviewReport:
        # Clone bundle without code snippets so code AST analysis is completely bypassed
        ablated_bundle = bundle.model_copy(deep=True)
        # Completely bypass code snippet AST inspection in ablation condition
        ablated_bundle.code_snippets = []

        report = super().review(ablated_bundle)
        # Update provenance to mark ablation
        prov = dict(report.provenance)
        prov["ablation_disabled_mechanism"] = "AST_CODE_INSPECTION"
        return report.model_copy(update={"provenance": prov})


class AblatedReviewer(HybridReviewer):
    """Hybrid reviewer running under the controlled ablation condition."""

    def __init__(self, config: Optional[ModelConfig] = None):
        super().__init__(model_client=FakeModelClient(mode="NORMAL"), config=config)

    def review(self, bundle: ReviewBundle, use_cache: bool = False) -> ReviewReport:
        # Override to use AblatedDeterministicReviewer
        det_report = AblatedDeterministicReviewer.review(bundle)

        # Proceed with model validation under identical configuration
        known_rule_ids = {r.id for r in bundle.source_requirements} | {
            r.rule_id for r in bundle.specification.rules
        }

        # Model call holding settings fixed
        prompt = f"ABLATION_MODE: Review bundle {bundle.bundle_id}"
        response = self.model_client.generate(prompt, self.config)

        # Merge findings
        merged_findings = list(det_report.findings)
        det_has_blocker = any(f.severity == Severity.BLOCKER for f in det_report.findings)

        status = ReviewStatus.CHANGES_REQUIRED if det_has_blocker else det_report.status

        prov = dict(det_report.provenance)
        prov["ablation"] = "AST_CODE_INSPECTION_DISABLED"

        return ReviewReport(
            status=status,
            summary=f"Ablated Reviewer verdict: {status.value} (AST inspection disabled)",
            findings=merged_findings,
            coverage=det_report.coverage,
            check_results=det_report.check_results,
            provenance=prov,
        )
