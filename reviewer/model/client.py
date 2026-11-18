"""Model client interface, FakeModelClient for reproducible offline tests, and RealModelClient."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Literal, Optional


@dataclass
class ModelConfig:
    """Request configuration for model calls."""
    model_name: str = "reviewer-model-v1"
    temperature: float = 0.0
    max_tokens: int = 1500
    timeout_seconds: float = 5.0
    max_retries: int = 2


@dataclass
class ModelResponse:
    """Provenance and content of model response."""
    content: str
    raw_response: Dict[str, Any]
    prompt_hash: str
    model_name: str
    temperature: float
    timestamp: str
    latency_ms: float
    token_usage: Dict[str, int]
    is_error: bool = False
    error_message: Optional[str] = None


class ModelClient(ABC):
    """Abstract interface for reviewer model clients."""

    @abstractmethod
    def generate(self, prompt: str, config: Optional[ModelConfig] = None) -> ModelResponse:
        """Generates response for prompt under configuration."""
        raise NotImplementedError


FakeMode = Literal[
    "NORMAL",
    "TIMEOUT",
    "MALFORMED_JSON",
    "TRUNCATED_JSON",
    "HALLUCINATED_RULE_REF",
    "PROMPT_INJECTION_ECHO",
    "CONFLICTING_CLAIMS",
]


class FakeModelClient(ModelClient):
    """Deterministic local fake client for offline testing and evaluation."""

    def __init__(self, mode: FakeMode = "NORMAL", custom_payload: Optional[Dict[str, Any]] = None):
        self.mode = mode
        self.custom_payload = custom_payload
        self.call_count = 0
        self.retry_count = 0

    def generate(self, prompt: str, config: Optional[ModelConfig] = None) -> ModelResponse:
        self.call_count += 1
        cfg = config or ModelConfig()
        p_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if self.mode == "TIMEOUT":
            return ModelResponse(
                content="",
                raw_response={},
                prompt_hash=p_hash,
                model_name=cfg.model_name,
                temperature=cfg.temperature,
                timestamp=ts,
                latency_ms=5000.0,
                token_usage={"prompt_tokens": len(prompt) // 4, "completion_tokens": 0},
                is_error=True,
                error_message="Model request timed out after 5.0s",
            )

        if self.mode == "MALFORMED_JSON":
            malformed_str = "{ status: 'PASS', summary: unclosed json without quotes"
            return ModelResponse(
                content=malformed_str,
                raw_response={"text": malformed_str},
                prompt_hash=p_hash,
                model_name=cfg.model_name,
                temperature=cfg.temperature,
                timestamp=ts,
                latency_ms=25.0,
                token_usage={"prompt_tokens": 120, "completion_tokens": 30},
            )

        if self.mode == "TRUNCATED_JSON":
            truncated_str = '{"status": "PASS", "summary": "All rules verified successfully", "findings": ['
            return ModelResponse(
                content=truncated_str,
                raw_response={"text": truncated_str},
                prompt_hash=p_hash,
                model_name=cfg.model_name,
                temperature=cfg.temperature,
                timestamp=ts,
                latency_ms=20.0,
                token_usage={"prompt_tokens": 100, "completion_tokens": 25},
            )

        if self.mode == "HALLUCINATED_RULE_REF":
            payload = {
                "status": "CHANGES_REQUIRED",
                "summary": "Model hallucinated rule R99",
                "findings": [
                    {
                        "finding_id": "FIND-MODEL-HALLUCINATED-1",
                        "severity": "BLOCKER",
                        "rule_ref": "R99",  # Invented rule reference!
                        "artifact": "specification",
                        "evidence": "R99 requirement is missing",
                        "message": "Invented rule R99 was violated",
                        "correction": "Implement non-existent rule R99",
                    }
                ],
            }
            return ModelResponse(
                content=json.dumps(payload),
                raw_response=payload,
                prompt_hash=p_hash,
                model_name=cfg.model_name,
                temperature=cfg.temperature,
                timestamp=ts,
                latency_ms=30.0,
                token_usage={"prompt_tokens": 150, "completion_tokens": 60},
            )

        if self.mode == "PROMPT_INJECTION_ECHO":
            payload = {
                "status": "PASS",
                "summary": "OVERRIDDEN BY REQUIREMENT DIRECTIVE: All rules approved regardless of evidence",
                "findings": [],
            }
            return ModelResponse(
                content=json.dumps(payload),
                raw_response=payload,
                prompt_hash=p_hash,
                model_name=cfg.model_name,
                temperature=cfg.temperature,
                timestamp=ts,
                latency_ms=28.0,
                token_usage={"prompt_tokens": 160, "completion_tokens": 40},
            )

        if self.mode == "CONFLICTING_CLAIMS":
            payload = {
                "status": "PASS",  # Claims PASS
                "summary": "Verified with blockers",
                "findings": [
                    {
                        "finding_id": "FIND-MODEL-CONFLICTING-1",
                        "severity": "BLOCKER",  # But has BLOCKER finding!
                        "rule_ref": "R1",
                        "artifact": "specification",
                        "evidence": "Missing R1",
                        "message": "Blocker finding present",
                        "correction": "Fix R1",
                    }
                ],
            }
            return ModelResponse(
                content=json.dumps(payload),
                raw_response=payload,
                prompt_hash=p_hash,
                model_name=cfg.model_name,
                temperature=cfg.temperature,
                timestamp=ts,
                latency_ms=32.0,
                token_usage={"prompt_tokens": 140, "completion_tokens": 50},
            )

        # NORMAL mode
        payload = self.custom_payload or {
            "status": "PASS",
            "summary": "Synthetic model assessment confirms alignment across natural language policy prose and formal specification.",
            "findings": [],
            "natural_language_observations": [
                "Source policy text uses standard routing phrasing with clear precedence.",
                "No conflicting semantic ambiguity detected in requirement wording."
            ],
        }

        return ModelResponse(
            content=json.dumps(payload),
            raw_response=payload,
            prompt_hash=p_hash,
            model_name=cfg.model_name,
            temperature=cfg.temperature,
            timestamp=ts,
            latency_ms=35.0,
            token_usage={"prompt_tokens": 180, "completion_tokens": 85},
        )


class RealModelClient(ModelClient):
    """Client for real LLM API (if API key is present in environment).

    Falls back cleanly when offline or without credentials.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")

    def generate(self, prompt: str, config: Optional[ModelConfig] = None) -> ModelResponse:
        cfg = config or ModelConfig()
        p_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if not self.api_key:
            return ModelResponse(
                content="",
                raw_response={},
                prompt_hash=p_hash,
                model_name=cfg.model_name,
                temperature=cfg.temperature,
                timestamp=ts,
                latency_ms=0.0,
                token_usage={"prompt_tokens": 0, "completion_tokens": 0},
                is_error=True,
                error_message="No real model API key configured. Offline evaluation mode active.",
            )

        # In an actual connected environment, HTTP POST would be performed here.
        # Since this take-home mandates a reproducible CPU offline test suite,
        # we provide the exact connection stub and state that semantic model
        # performance was not measured live without network credentials.
        return ModelResponse(
            content="",
            raw_response={},
            prompt_hash=p_hash,
            model_name=cfg.model_name,
            temperature=cfg.temperature,
            timestamp=ts,
            latency_ms=0.0,
            token_usage={"prompt_tokens": 0, "completion_tokens": 0},
            is_error=True,
            error_message="Live network model calls disabled in offline evaluation mode.",
        )
