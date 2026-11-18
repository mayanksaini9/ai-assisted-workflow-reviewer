"""Pydantic schemas and contracts for all reviewer artifacts."""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


class Severity(str, Enum):
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    INFO = "INFO"


class ReviewStatus(str, Enum):
    PASS = "PASS"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    INCOMPLETE = "INCOMPLETE"


class EvidenceLocation(BaseModel):
    """Anchored evidence location across source, spec, workflow, or code."""
    file_path: Optional[str] = None
    section: Optional[str] = None
    line_number: Optional[int] = None
    json_pointer: Optional[str] = None
    node_id: Optional[str] = None

    def display(self) -> str:
        parts = []
        if self.file_path:
            parts.append(self.file_path)
        if self.section:
            parts.append(f"section:{self.section}")
        if self.node_id:
            parts.append(f"node:{self.node_id}")
        if self.line_number is not None:
            parts.append(f"L{self.line_number}")
        if self.json_pointer:
            parts.append(self.json_pointer)
        return " -> ".join(parts) if parts else "unspecified location"


class RuleException(BaseModel):
    """Exception attached to a rule."""
    exception_id: str
    condition_text: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    action: str
    precedence_offset: int = 0
    description: Optional[str] = None


class SourceRequirement(BaseModel):
    """Anchored written source policy requirement."""
    id: str
    text: str
    anchor: EvidenceLocation
    condition_text: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    action: str
    precedence: int
    exceptions: List[RuleException] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SpecificationRule(BaseModel):
    """Rule in structured specification."""
    rule_id: str
    description: str
    condition_text: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    action: str
    precedence: int
    location: EvidenceLocation


class Specification(BaseModel):
    """Structured specification artifact."""
    spec_id: str
    version: str
    rules: List[SpecificationRule]
    created_at: Optional[str] = None


class WorkflowBranch(BaseModel):
    """Branch from a decision node to another node."""
    branch_id: str
    condition_text: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    target_node_id: str
    label: Optional[str] = None


class WorkflowNode(BaseModel):
    """Node in workflow decision graph."""
    node_id: str
    node_type: Literal["decision", "action", "entry", "exit"]
    action: Optional[str] = None  # Populated for action/exit nodes
    branches: List[WorkflowBranch] = Field(default_factory=list)
    rule_ref: Optional[str] = None
    location: EvidenceLocation


class WorkflowGraph(BaseModel):
    """Versioned workflow decision graph."""
    workflow_id: str
    version: str
    entry_node_id: str
    nodes: Dict[str, WorkflowNode]  # Indexed by node_id

    @field_validator("nodes", mode="before")
    @classmethod
    def transform_nodes_list(cls, v: Any) -> Dict[str, Any]:
        if isinstance(v, list):
            res = {}
            for item in v:
                if isinstance(item, dict):
                    res[item["node_id"]] = item
                elif hasattr(item, "node_id"):
                    res[item.node_id] = item
            return res
        return v


class CodeSnippet(BaseModel):
    """Linked Python code snippet."""
    snippet_id: str
    rule_ref: Optional[str] = None
    function_name: Optional[str] = None
    code: str
    docstring: Optional[str] = None
    location: EvidenceLocation


class ApprovalRecord(BaseModel):
    """Top-level recorded change request and approval."""
    change_id: str
    rule_ref: str
    field: str
    old_value: Any
    new_value: Any
    status: Literal["APPROVED", "REJECTED", "PENDING"]
    approver: str
    timestamp: str
    version: str
    rationale: Optional[str] = None
    is_top_level: bool = True


class ReviewBundle(BaseModel):
    """Complete bundle of artifacts submitted for review."""
    bundle_id: str
    description: str
    version: str
    source_requirements: List[SourceRequirement]
    specification: Specification
    workflow_graph: WorkflowGraph
    code_snippets: List[CodeSnippet]
    approval_records: List[ApprovalRecord] = Field(default_factory=list)
    expected_verdict: Optional[ReviewStatus] = None
    expected_defects: List[str] = Field(default_factory=list)
    test_group: Optional[Literal["tuning", "reserved_evaluation"]] = "tuning"


class Counterexample(BaseModel):
    """Concrete ticket showing diverging behavior between artifacts."""
    ticket: Dict[str, Any]
    expected_route: str
    actual_route: str
    divergence_reason: str
    evidence: Dict[str, str] = Field(default_factory=dict)


class ReviewFinding(BaseModel):
    """Individual structured defect or warning."""
    finding_id: str
    severity: Severity
    rule_ref: Optional[str] = None
    artifact: Literal["source", "specification", "workflow", "code", "approval", "model", "scope"]
    version: Optional[str] = None
    location: EvidenceLocation
    evidence: str
    message: str
    correction: Optional[str] = None
    counterexample: Optional[Counterexample] = None


class CoverageReport(BaseModel):
    """Coverage of source policies across specification, workflow, and code."""
    total_source_requirements: int
    reviewed_requirements: List[str]
    omitted_requirements: List[str]
    unsupported_elements: List[Dict[str, str]] = Field(default_factory=list)
    completeness_ratio: float


class ReviewReport(BaseModel):
    """Final comprehensive review verdict and findings."""
    status: ReviewStatus
    summary: str
    findings: List[ReviewFinding]
    coverage: CoverageReport
    check_results: Dict[str, Any]
    provenance: Dict[str, Any]
