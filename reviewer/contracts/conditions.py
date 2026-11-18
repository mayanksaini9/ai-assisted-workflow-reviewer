"""Condition AST and normalization for the Workflow Reviewer DSL."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union
import re


# Standard time unit conversions to canonical seconds
TIME_UNIT_FACTORS: Dict[str, float] = {
    "s": 1.0,
    "sec": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
}


@dataclass(frozen=True)
class Duration:
    """Normalized time duration representation."""
    value: float
    unit: str  # Original declared unit
    canonical_seconds: float = field(init=False)

    def __post_init__(self):
        norm_unit = self.unit.lower().strip()
        factor = TIME_UNIT_FACTORS.get(norm_unit)
        if factor is None:
            raise ValueError(f"Unsupported time unit: '{self.unit}'")
        object.__setattr__(self, "canonical_seconds", round(self.value * factor, 6))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Duration):
            return abs(self.canonical_seconds - other.canonical_seconds) < 1e-6
        return False

    def __lt__(self, other: Duration) -> bool:
        return self.canonical_seconds < other.canonical_seconds

    def __le__(self, other: Duration) -> bool:
        return self.canonical_seconds <= other.canonical_seconds

    def __gt__(self, other: Duration) -> bool:
        return self.canonical_seconds > other.canonical_seconds

    def __ge__(self, other: Duration) -> bool:
        return self.canonical_seconds >= other.canonical_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "Duration",
            "value": self.value,
            "unit": self.unit,
            "canonical_seconds": self.canonical_seconds,
        }


ComparisonOp = Literal[">", ">=", "<", "<=", "==", "!="]


@dataclass(frozen=True)
class ConditionNode:
    """Base class for all condition AST nodes."""

    def evaluate(self, context: Dict[str, Any]) -> bool:
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    def get_referenced_fields(self) -> Set[str]:
        raise NotImplementedError


@dataclass(frozen=True)
class TrueCondition(ConditionNode):
    """Fallback / default condition representing 'Otherwise' or unconditional match."""

    def evaluate(self, context: Dict[str, Any]) -> bool:
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "True"}

    def get_referenced_fields(self) -> Set[str]:
        return set()


@dataclass(frozen=True)
class FalseCondition(ConditionNode):
    """Contradiction / impossible condition."""

    def evaluate(self, context: Dict[str, Any]) -> bool:
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "False"}

    def get_referenced_fields(self) -> Set[str]:
        return set()


@dataclass(frozen=True)
class MissingCondition(ConditionNode):
    """Checks whether a field is missing, None, or empty string."""
    field_name: str
    is_missing: bool = True  # True: missing; False: present

    def evaluate(self, context: Dict[str, Any]) -> bool:
        val = context.get(self.field_name)
        missing = (val is None) or (val == "")
        return missing if self.is_missing else not missing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "Missing",
            "field": self.field_name,
            "is_missing": self.is_missing,
        }

    def get_referenced_fields(self) -> Set[str]:
        return {self.field_name}


@dataclass(frozen=True)
class ComparisonCondition(ConditionNode):
    """Numeric or scalar comparison with boundary semantics."""
    field_name: str
    operator: ComparisonOp
    value: Union[float, int, str, bool, Duration]

    def evaluate(self, context: Dict[str, Any]) -> bool:
        field_val = context.get(self.field_name)
        if field_val is None:
            return False

        target_val = self.value
        # If comparing duration
        if isinstance(target_val, Duration):
            if isinstance(field_val, (int, float)):
                # Assume field_val is already canonical seconds or matches unit
                f_secs = float(field_val)
            elif isinstance(field_val, Duration):
                f_secs = field_val.canonical_seconds
            elif isinstance(field_val, str):
                # Try parsing string duration, e.g. "30m"
                m = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?$", field_val.strip())
                if m:
                    v = float(m.group(1))
                    u = m.group(2) or "minutes"
                    f_secs = Duration(v, u).canonical_seconds
                else:
                    return False
            else:
                return False
            t_secs = target_val.canonical_seconds
            return self._cmp(f_secs, self.operator, t_secs)

        # Standard numeric / string comparison
        if isinstance(target_val, (int, float)) and isinstance(field_val, (int, float)):
            return self._cmp(float(field_val), self.operator, float(target_val))

        if self.operator == "==":
            return str(field_val).lower() == str(target_val).lower()
        elif self.operator == "!=":
            return str(field_val).lower() != str(target_val).lower()

        return False

    @staticmethod
    def _cmp(a: float, op: ComparisonOp, b: float) -> bool:
        if op == ">":
            return a > b
        elif op == ">=":
            return a >= b
        elif op == "<":
            return a < b
        elif op == "<=":
            return a <= b
        elif op == "==":
            return abs(a - b) < 1e-6
        elif op == "!=":
            return abs(a - b) >= 1e-6
        return False

    def to_dict(self) -> Dict[str, Any]:
        val_repr = self.value.to_dict() if isinstance(self.value, Duration) else self.value
        return {
            "type": "Comparison",
            "field": self.field_name,
            "operator": self.operator,
            "value": val_repr,
        }

    def get_referenced_fields(self) -> Set[str]:
        return {self.field_name}


@dataclass(frozen=True)
class InSetCondition(ConditionNode):
    """Membership check: field in set of values."""
    field_name: str
    values: Tuple[Any, ...]
    negated: bool = False

    def evaluate(self, context: Dict[str, Any]) -> bool:
        val = context.get(self.field_name)
        if val is None:
            return self.negated
        norm_val = str(val).lower()
        norm_set = {str(v).lower() for v in self.values}
        contained = norm_val in norm_set
        return not contained if self.negated else contained

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "InSet",
            "field": self.field_name,
            "values": list(self.values),
            "negated": self.negated,
        }

    def get_referenced_fields(self) -> Set[str]:
        return {self.field_name}


@dataclass(frozen=True)
class LogicalCondition(ConditionNode):
    """Nested boolean operations: AND, OR, NOT."""
    operator: Literal["AND", "OR", "NOT"]
    children: Tuple[ConditionNode, ...]

    def evaluate(self, context: Dict[str, Any]) -> bool:
        if self.operator == "AND":
            return all(c.evaluate(context) for c in self.children)
        elif self.operator == "OR":
            return any(c.evaluate(context) for c in self.children)
        elif self.operator == "NOT":
            if not self.children:
                return True
            return not self.children[0].evaluate(context)
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "Logical",
            "operator": self.operator,
            "children": [c.to_dict() for c in self.children],
        }

    def get_referenced_fields(self) -> Set[str]:
        fields: Set[str] = set()
        for c in self.children:
            fields.update(c.get_referenced_fields())
        return fields


@dataclass(frozen=True)
class UnsupportedCondition(ConditionNode):
    """Represents an unparseable, ambiguous, or out-of-scope condition."""
    raw_text: str
    reason: str

    def evaluate(self, context: Dict[str, Any]) -> bool:
        # Cannot evaluate unsupported constructs deterministically
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "Unsupported",
            "raw_text": self.raw_text,
            "reason": self.reason,
        }

    def get_referenced_fields(self) -> Set[str]:
        return set()
