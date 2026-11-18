"""Model integration, clients, prompts, and failure handling."""

from reviewer.model.client import (
    FakeModelClient,
    FakeMode,
    ModelClient,
    ModelConfig,
    ModelResponse,
    RealModelClient,
)
from reviewer.model.hybrid_reviewer import HybridReviewer
from reviewer.model.prompts import SYSTEM_PROMPT, build_reviewer_prompt
from reviewer.model.validator import ModelResponseValidator

__all__ = [
    "FakeMode",
    "FakeModelClient",
    "HybridReviewer",
    "ModelClient",
    "ModelConfig",
    "ModelResponse",
    "ModelResponseValidator",
    "RealModelClient",
    "SYSTEM_PROMPT",
    "build_reviewer_prompt",
]
