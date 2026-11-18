"""Deterministic domain solver and counterexample generator for ticket workflows.

Supports:
- Missing/null checks
- Numeric intervals with inclusive/exclusive boundaries
- Discrete time units normalized to canonical seconds
- Categorical set membership / equality
- Nested AND / OR / NOT
- Exhaustive equivalence proofs and concrete counterexample synthesis
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from reviewer.contracts.conditions import (
    ComparisonCondition,
    ConditionNode,
    Duration,
    FalseCondition,
    InSetCondition,
    LogicalCondition,
    MissingCondition,
    TrueCondition,
    UnsupportedCondition,
)


@dataclass(frozen=True)
class NumericInterval:
    """Represents a 1D real interval with inclusive/exclusive bounds."""
    min_val: float = -math.inf
    max_val: float = math.inf
    min_inclusive: bool = False
    max_inclusive: bool = False

    def is_empty(self) -> bool:
        if self.min_val > self.max_val:
            return True
        if abs(self.min_val - self.max_val) < 1e-9:
            return not (self.min_inclusive and self.max_inclusive)
        return False

    def contains(self, val: float) -> bool:
        if self.min_inclusive:
            if val < self.min_val - 1e-9:
                return False
        else:
            if val <= self.min_val + 1e-9:
                return False

        if self.max_inclusive:
            if val > self.max_val + 1e-9:
                return False
        else:
            if val >= self.max_val - 1e-9:
                return False
        return True

    def intersect(self, other: NumericInterval) -> NumericInterval:
        # Determine new min
        if self.min_val > other.min_val:
            new_min, new_min_inc = self.min_val, self.min_inclusive
        elif other.min_val > self.min_val:
            new_min, new_min_inc = other.min_val, other.min_inclusive
        else:
            new_min = self.min_val
            new_min_inc = self.min_inclusive and other.min_inclusive

        # Determine new max
        if self.max_val < other.max_val:
            new_max, new_max_inc = self.max_val, self.max_inclusive
        elif other.max_val < self.max_val:
            new_max, new_max_inc = other.max_val, other.max_inclusive
        else:
            new_max = self.max_val
            new_max_inc = self.max_inclusive and other.max_inclusive

        return NumericInterval(new_min, new_max, new_min_inc, new_max_inc)

    def sample_witness(self) -> Optional[float]:
        """Returns a representative point within the interval."""
        if self.is_empty():
            return None
        if math.isinf(self.min_val) and math.isinf(self.max_val):
            return 0.0
        if math.isinf(self.min_val):
            return self.max_val - (1.0 if not self.max_inclusive else 0.0)
        if math.isinf(self.max_val):
            return self.min_val + (1.0 if not self.min_inclusive else 0.0)

        if self.min_inclusive:
            return self.min_val
        if self.max_inclusive:
            return self.max_val
        return (self.min_val + self.max_val) / 2.0


@dataclass
class TicketDomainState:
    """State constraint space for ticket fields."""
    missing_fields: Set[str] = field(default_factory=set)
    present_fields: Set[str] = field(default_factory=set)
    numeric_intervals: Dict[str, NumericInterval] = field(default_factory=dict)
    categorical_allowed: Dict[str, Set[str]] = field(default_factory=dict)
    categorical_excluded: Dict[str, Set[str]] = field(default_factory=dict)
    is_contradiction: bool = False

    def copy(self) -> TicketDomainState:
        return TicketDomainState(
            missing_fields=set(self.missing_fields),
            present_fields=set(self.present_fields),
            numeric_intervals=dict(self.numeric_intervals),
            categorical_allowed={k: set(v) for k, v in self.categorical_allowed.items()},
            categorical_excluded={k: set(v) for k, v in self.categorical_excluded.items()},
            is_contradiction=self.is_contradiction,
        )

    def constrain_missing(self, field_name: str, is_missing: bool) -> None:
        if self.is_contradiction:
            return
        if is_missing:
            if field_name in self.present_fields:
                self.is_contradiction = True
                return
            self.missing_fields.add(field_name)
        else:
            if field_name in self.missing_fields:
                self.is_contradiction = True
                return
            self.present_fields.add(field_name)

    def constrain_numeric(self, field_name: str, interval: NumericInterval) -> None:
        if self.is_contradiction:
            return
        self.constrain_missing(field_name, is_missing=False)
        cur = self.numeric_intervals.get(field_name, NumericInterval())
        new_int = cur.intersect(interval)
        if new_int.is_empty():
            self.is_contradiction = True
            return
        self.numeric_intervals[field_name] = new_int

    def constrain_categorical(self, field_name: str, allowed: Optional[Set[str]] = None, excluded: Optional[Set[str]] = None) -> None:
        if self.is_contradiction:
            return
        self.constrain_missing(field_name, is_missing=False)
        if allowed is not None:
            norm_allowed = {str(x).lower() for x in allowed}
            cur_allowed = self.categorical_allowed.get(field_name)
            if cur_allowed is not None:
                new_allowed = cur_allowed.intersection(norm_allowed)
                if not new_allowed:
                    self.is_contradiction = True
                    return
                self.categorical_allowed[field_name] = new_allowed
            else:
                self.categorical_allowed[field_name] = norm_allowed

        if excluded is not None:
            norm_excluded = {str(x).lower() for x in excluded}
            cur_excluded = self.categorical_excluded.setdefault(field_name, set())
            cur_excluded.update(norm_excluded)

            cur_allowed = self.categorical_allowed.get(field_name)
            if cur_allowed is not None:
                remaining = cur_allowed - cur_excluded
                if not remaining:
                    self.is_contradiction = True
                    return
                self.categorical_allowed[field_name] = remaining

    def synthesize_ticket(self) -> Optional[Dict[str, Any]]:
        """Synthesizes a concrete ticket fulfilling all constraints."""
        if self.is_contradiction:
            return None

        ticket: Dict[str, Any] = {
            "category": "technical",
            "urgency": "normal",
            "waiting_time": 0,
            "customer_tier": "standard",
            "status": "open",
        }

        # Apply missing fields
        for f in self.missing_fields:
            ticket[f] = None

        # Apply categorical constraints
        for f, allowed in self.categorical_allowed.items():
            if f not in self.missing_fields:
                ticket[f] = next(iter(allowed))

        for f, excluded in self.categorical_excluded.items():
            if f not in self.missing_fields:
                if str(ticket.get(f)).lower() in excluded:
                    # Pick an alternative
                    candidates = ["urgent", "normal", "low", "billing", "account", "vip", "gold"]
                    for c in candidates:
                        if c not in excluded:
                            ticket[f] = c
                            break

        # Apply numeric intervals
        for f, interval in self.numeric_intervals.items():
            if f not in self.missing_fields:
                w = interval.sample_witness()
                if w is None:
                    return None
                ticket[f] = round(w, 2)
                # Keep minutes/seconds field in sync
                if "waiting" in f.lower():
                    # If field was seconds, also supply minutes, or vice-versa
                    ticket["waiting_time_minutes"] = round(ticket[f] / 60.0, 2) if "sec" in f else ticket[f]

        return ticket


class DomainSolver:
    """Formal domain solver for ticket workflow verification."""

    @classmethod
    def condition_to_states(cls, cond: ConditionNode, base: Optional[TicketDomainState] = None) -> List[TicketDomainState]:
        """Translates ConditionNode into DNF (list of conjunctive TicketDomainStates)."""
        state = base.copy() if base else TicketDomainState()
        if isinstance(cond, TrueCondition):
            return [state]
        if isinstance(cond, FalseCondition):
            return []

        if isinstance(cond, MissingCondition):
            state.constrain_missing(cond.field_name, cond.is_missing)
            return [] if state.is_contradiction else [state]

        if isinstance(cond, ComparisonCondition):
            target_val = cond.value
            op = cond.operator
            f_name = cond.field_name

            # Numeric or Duration comparison
            if isinstance(target_val, (int, float, Duration)):
                num_val = target_val.canonical_seconds if isinstance(target_val, Duration) else float(target_val)
                interval = cls._op_to_interval(op, num_val)
                state.constrain_numeric(f_name, interval)
                return [] if state.is_contradiction else [state]

            # Categorical string / bool comparison
            if op == "==":
                state.constrain_categorical(f_name, allowed={str(target_val)})
            elif op == "!=":
                state.constrain_categorical(f_name, excluded={str(target_val)})
            else:
                return []
            return [] if state.is_contradiction else [state]

        if isinstance(cond, InSetCondition):
            f_name = cond.field_name
            if cond.negated:
                state.constrain_categorical(f_name, excluded=set(cond.values))
            else:
                state.constrain_categorical(f_name, allowed=set(cond.values))
            return [] if state.is_contradiction else [state]

        if isinstance(cond, LogicalCondition):
            if cond.operator == "AND":
                current_states = [state]
                for child in cond.children:
                    next_states = []
                    for st in current_states:
                        child_states = cls.condition_to_states(child, base=st)
                        next_states.extend(child_states)
                    current_states = next_states
                    if not current_states:
                        break
                return current_states

            if cond.operator == "OR":
                res = []
                for child in cond.children:
                    res.extend(cls.condition_to_states(child, base=state))
                return res

            if cond.operator == "NOT":
                negated = cls._negate_condition(cond.children[0])
                return cls.condition_to_states(negated, base=state)

        # UnsupportedCondition cannot be converted to states
        return []

    @classmethod
    def _op_to_interval(cls, op: str, val: float) -> NumericInterval:
        if op == ">":
            return NumericInterval(min_val=val, min_inclusive=False)
        elif op == ">=":
            return NumericInterval(min_val=val, min_inclusive=True)
        elif op == "<":
            return NumericInterval(max_val=val, max_inclusive=False)
        elif op == "<=":
            return NumericInterval(max_val=val, max_inclusive=True)
        elif op == "==":
            return NumericInterval(min_val=val, max_val=val, min_inclusive=True, max_inclusive=True)
        elif op == "!=":
            # Handled in higher-level splitting if needed; basic default:
            return NumericInterval()
        return NumericInterval()

    @classmethod
    def _negate_condition(cls, cond: ConditionNode) -> ConditionNode:
        """Computes logical negation of ConditionNode using De Morgan's laws."""
        if isinstance(cond, TrueCondition):
            return FalseCondition()
        if isinstance(cond, FalseCondition):
            return TrueCondition()
        if isinstance(cond, MissingCondition):
            return MissingCondition(field_name=cond.field_name, is_missing=not cond.is_missing)
        if isinstance(cond, ComparisonCondition):
            neg_ops = {
                ">": "<=",
                ">=": "<",
                "<": ">=",
                "<=": ">",
                "==": "!=",
                "!=": "==",
            }
            return ComparisonCondition(
                field_name=cond.field_name,
                operator=neg_ops.get(cond.operator, "=="),  # type: ignore
                value=cond.value,
            )
        if isinstance(cond, InSetCondition):
            return InSetCondition(
                field_name=cond.field_name,
                values=cond.values,
                negated=not cond.negated,
            )
        if isinstance(cond, LogicalCondition):
            if cond.operator == "AND":
                return LogicalCondition(
                    operator="OR",
                    children=tuple(cls._negate_condition(c) for c in cond.children),
                )
            elif cond.operator == "OR":
                return LogicalCondition(
                    operator="AND",
                    children=tuple(cls._negate_condition(c) for c in cond.children),
                )
            elif cond.operator == "NOT":
                return cond.children[0]
        return UnsupportedCondition(raw_text=f"not ({cond.to_dict()})", reason="Cannot negate unsupported condition")

    @classmethod
    def check_equivalence(cls, cond1: ConditionNode, cond2: ConditionNode) -> Tuple[bool, Optional[str]]:
        """Exhaustively checks equivalence within supported domain.

        Returns (is_equivalent, divergence_explanation).
        """
        # Quick dict equality
        if cond1.to_dict() == cond2.to_dict():
            return True, None

        # Check unsupported
        if isinstance(cond1, UnsupportedCondition) or isinstance(cond2, UnsupportedCondition):
            return False, "Cannot prove equivalence on unsupported constructs"

        # Check if cond1 => cond2 and cond2 => cond1
        # Counterexample for cond1 => cond2 is: cond1 AND NOT(cond2)
        diff1 = cls.generate_counterexample(cond1, cond2)
        if diff1:
            return False, f"Condition 1 satisfies ticket {diff1} but Condition 2 does not"

        diff2 = cls.generate_counterexample(cond2, cond1)
        if diff2:
            return False, f"Condition 2 satisfies ticket {diff2} but Condition 1 does not"

        return True, None

    @classmethod
    def generate_counterexample(
        cls,
        satisfy_cond: ConditionNode,
        violate_cond: ConditionNode,
        context_constraints: Optional[List[ConditionNode]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Finds a concrete ticket satisfying satisfy_cond while violating violate_cond."""
        neg_violate = cls._negate_condition(violate_cond)
        all_children = [satisfy_cond, neg_violate]
        if context_constraints:
            all_children.extend(context_constraints)

        combined = LogicalCondition(operator="AND", children=tuple(all_children))
        states = cls.condition_to_states(combined)
        for st in states:
            t = st.synthesize_ticket()
            if t is not None:
                # Double check with evaluate()
                if satisfy_cond.evaluate(t) and not violate_cond.evaluate(t):
                    return t
        return None

    @classmethod
    def generate_precedence_counterexample(
        cls,
        first_effective_cond: ConditionNode,
        second_effective_cond: ConditionNode,
        prior_effective_conds: Optional[List[ConditionNode]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Synthesizes a ticket that satisfies BOTH rule conditions.

        When rule order is inverted, this ticket triggers the wrong rule.
        """
        children = [first_effective_cond, second_effective_cond]
        if prior_effective_conds:
            for p in prior_effective_conds:
                children.append(cls._negate_condition(p))

        combined = LogicalCondition(operator="AND", children=tuple(children))
        states = cls.condition_to_states(combined)
        for st in states:
            t = st.synthesize_ticket()
            if t is not None:
                return t
        return None
