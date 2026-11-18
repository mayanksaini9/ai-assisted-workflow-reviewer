"""Parser for the Workflow Reviewer DSL and restricted Python AST."""

from __future__ import annotations
import ast
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from reviewer.contracts.conditions import (
    ComparisonCondition,
    ConditionNode,
    Duration,
    FalseCondition,
    InSetCondition,
    LogicalCondition,
    MissingCondition,
    TIME_UNIT_FACTORS,
    TrueCondition,
    UnsupportedCondition,
)


def parse_condition_dict(data: Optional[Dict[str, Any]]) -> ConditionNode:
    """Reconstitute ConditionNode from serialized dictionary."""
    if not data:
        return TrueCondition()

    node_type = data.get("type")
    if node_type == "True":
        return TrueCondition()
    if node_type == "False":
        return FalseCondition()

    if node_type == "Missing":
        return MissingCondition(
            field_name=data.get("field", ""),
            is_missing=data.get("is_missing", True),
        )

    if node_type == "Comparison":
        val = data.get("value")
        if isinstance(val, dict) and val.get("type") == "Duration":
            val_obj: Union[float, int, str, bool, Duration] = Duration(
                value=float(val["value"]),
                unit=val["unit"],
            )
        else:
            val_obj = val
        return ComparisonCondition(
            field_name=data.get("field", ""),
            operator=data.get("operator", "=="),
            value=val_obj,
        )

    if node_type == "InSet":
        return InSetCondition(
            field_name=data.get("field", ""),
            values=tuple(data.get("values", [])),
            negated=data.get("negated", False),
        )

    if node_type == "Logical":
        op = data.get("operator", "AND")
        children = [parse_condition_dict(c) for c in data.get("children", [])]
        return LogicalCondition(operator=op, children=tuple(children))

    if node_type == "Unsupported":
        return UnsupportedCondition(
            raw_text=data.get("raw_text", ""),
            reason=data.get("reason", "Serialized as unsupported"),
        )

    return UnsupportedCondition(
        raw_text=str(data),
        reason=f"Unknown condition dictionary format: {node_type}",
    )


class DSLParser:
    """Parses textual policy condition expressions into structured ConditionNode AST."""

    # Time regex: e.g. "30 minutes", "0.5 hours", "1800 s"
    TIME_RE = re.compile(
        r"^(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>" + "|".join(re.escape(u) for u in sorted(TIME_UNIT_FACTORS.keys(), key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )

    # Missing regex: e.g. "missing(category)", "category is null", "category is missing", "category is None"
    MISSING_RE = re.compile(
        r"^(?:missing\s*\(\s*(?P<f1>[a-zA-Z_]\w*)\s*\)|(?P<f2>[a-zA-Z_]\w*)\s+is\s+(?:null|none|missing))$",
        re.IGNORECASE,
    )

    # InSet regex: e.g. "tier in ['VIP', 'platinum']" or "status not in ['closed']"
    INSET_RE = re.compile(
        r"^(?P<field>[a-zA-Z_]\w*)\s+(?P<neg>not\s+in|in)\s+\[(?P<items>[^\]]+)\]$",
        re.IGNORECASE,
    )

    # Comparison regex: e.g. "waiting >= 30 minutes", "urgency == 'urgent'"
    CMP_RE = re.compile(
        r"^(?P<field>[a-zA-Z_]\w*)\s*(?P<op>>=|<=|>|<|==|!=)\s*(?P<val>.+)$",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, expr: Optional[str]) -> ConditionNode:
        if not expr:
            return TrueCondition()

        trimmed = expr.strip()
        if not trimmed or trimmed.lower() in ("otherwise", "all remaining", "default", "true", "*", "else"):
            return TrueCondition()

        if trimmed.lower() in ("false", "none", "never"):
            return FalseCondition()

        # Handle top-level OR (outside parentheses)
        or_parts = cls._split_top_level(trimmed, " or ")
        if len(or_parts) > 1:
            return LogicalCondition(
                operator="OR",
                children=tuple(cls.parse(p) for p in or_parts),
            )

        # Handle top-level AND (outside parentheses)
        and_parts = cls._split_top_level(trimmed, " and ")
        if len(and_parts) > 1:
            return LogicalCondition(
                operator="AND",
                children=tuple(cls.parse(p) for p in and_parts),
            )

        # Handle NOT
        if trimmed.lower().startswith("not "):
            inner = cls.parse(trimmed[4:].strip())
            return LogicalCondition(operator="NOT", children=(inner,))

        # Strip outer enclosing parens if any
        if trimmed.startswith("(") and trimmed.endswith(")"):
            # Check if matching
            if cls._is_enclosed_in_parens(trimmed):
                return cls.parse(trimmed[1:-1].strip())

        # Check Missing
        m_missing = cls.MISSING_RE.match(trimmed)
        if m_missing:
            f_name = m_missing.group("f1") or m_missing.group("f2")
            return MissingCondition(field_name=f_name, is_missing=True)

        # Check InSet
        m_inset = cls.INSET_RE.match(trimmed)
        if m_inset:
            f_name = m_inset.group("field")
            is_neg = "not" in m_inset.group("neg").lower()
            items_raw = m_inset.group("items").split(",")
            clean_items = []
            for item in items_raw:
                val = item.strip().strip("'\"")
                clean_items.append(val)
            return InSetCondition(field_name=f_name, values=tuple(clean_items), negated=is_neg)

        # Check Comparison
        m_cmp = cls.CMP_RE.match(trimmed)
        if m_cmp:
            f_name = m_cmp.group("field")
            op = m_cmp.group("op")
            raw_val = m_cmp.group("val").strip()

            # Check if duration
            m_time = cls.TIME_RE.match(raw_val)
            if m_time:
                num = float(m_time.group("val"))
                unit = m_time.group("unit")
                return ComparisonCondition(
                    field_name=f_name,
                    operator=op,  # type: ignore
                    value=Duration(value=num, unit=unit),
                )

            # Check numeric scalar
            try:
                if "." in raw_val:
                    num_val = float(raw_val)
                else:
                    num_val = int(raw_val)
                return ComparisonCondition(
                    field_name=f_name,
                    operator=op,  # type: ignore
                    value=num_val,
                )
            except ValueError:
                pass

            # String or boolean literal
            clean_val: Union[str, bool] = raw_val.strip("'\"")
            if clean_val.lower() == "true":
                clean_val = True
            elif clean_val.lower() == "false":
                clean_val = False

            return ComparisonCondition(
                field_name=f_name,
                operator=op,  # type: ignore
                value=clean_val,
            )

        # If it doesn't match standard patterns, preserve it as Unsupported
        return UnsupportedCondition(
            raw_text=trimmed,
            reason="Unrecognized DSL pattern or ambiguous expression",
        )

    @classmethod
    def _is_enclosed_in_parens(cls, s: str) -> bool:
        if not (s.startswith("(") and s.endswith(")")):
            return False
        depth = 0
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i < len(s) - 1:
                    return False
        return depth == 0

    @classmethod
    def _split_top_level(cls, s: str, delimiter: str) -> List[str]:
        parts = []
        depth = 0
        cur = []
        delim_len = len(delimiter)
        i = 0
        s_lower = s.lower()
        while i < len(s):
            ch = s[i]
            if ch == "(":
                depth += 1
                cur.append(ch)
                i += 1
            elif ch == ")":
                depth -= 1
                cur.append(ch)
                i += 1
            elif depth == 0 and s_lower[i : i + delim_len] == delimiter:
                parts.append("".join(cur).strip())
                cur = []
                i += delim_len
            else:
                cur.append(ch)
                i += 1
        if cur:
            parts.append("".join(cur).strip())
        return parts if len(parts) > 1 else [s]


class RestrictedPythonASTAnalyzer:
    """Analyzes Python code snippets in a restricted subset without execution."""

    ALLOWED_NODE_TYPES = (
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.Return,
        ast.If,
        ast.Compare,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Name,
        ast.Constant,
        ast.Subscript,
        ast.Attribute,
        ast.Call,  # strictly ticket.get(...) only
        ast.Pass,
        ast.Expr,
        ast.Load,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
        ast.And,
        ast.Or,
        ast.Not,
    )

    @classmethod
    def validate_syntax_and_safety(cls, code: str) -> Tuple[bool, Optional[str], Optional[ast.AST]]:
        """Verifies syntax and ensures code does not contain untrusted or unsafe constructs."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Syntax error in Python snippet: {e}", None

        for node in ast.walk(tree):
            if not isinstance(node, cls.ALLOWED_NODE_TYPES):
                return (
                    False,
                    f"Unsupported or dangerous Python construct: {type(node).__name__}",
                    tree,
                )
            # Check function calls: only allow .get() on ticket/dict
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                    pass
                else:
                    return (
                        False,
                        f"Disallowed function call '{ast.unparse(node.func) if hasattr(ast, 'unparse') else 'call'}'. Only dict.get() allowed.",
                        tree,
                    )
        return True, None, tree

    @classmethod
    def extract_branches(cls, code: str) -> List[Tuple[ConditionNode, str]]:
        """Extracts (ConditionNode, return_value) branches from restricted Python function."""
        is_safe, error_msg, tree = cls.validate_syntax_and_safety(code)
        if not is_safe or not tree:
            return [(UnsupportedCondition(raw_text=code, reason=error_msg or "Unsafe code"), "ERROR")]

        branches: List[Tuple[ConditionNode, str]] = []

        # Find FunctionDef
        func_def = None
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef):
                func_def = stmt
                break

        body_stmts = func_def.body if func_def else tree.body
        cls._walk_statements(body_stmts, branches)
        return branches

    @classmethod
    def _walk_statements(cls, stmts: List[ast.stmt], branches: List[Tuple[ConditionNode, str]]):
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                cond = cls._ast_test_to_condition(stmt.test)
                # Find return inside if body
                ret_val = cls._find_return_value(stmt.body)
                if ret_val is not None:
                    branches.append((cond, ret_val))
                else:
                    # Recursive walk in if body
                    cls._walk_statements(stmt.body, branches)

                # Process orelse (elif or else)
                if stmt.orelse:
                    if len(stmt.orelse) == 1 and isinstance(stmt.orelse[0], ast.If):
                        cls._walk_statements(stmt.orelse, branches)
                    else:
                        else_ret = cls._find_return_value(stmt.orelse)
                        if else_ret is not None:
                            branches.append((TrueCondition(), else_ret))
                        else:
                            cls._walk_statements(stmt.orelse, branches)
            elif isinstance(stmt, ast.Return):
                val = cls._extract_constant(stmt.value)
                if val is not None:
                    branches.append((TrueCondition(), str(val)))

    @classmethod
    def _find_return_value(cls, stmts: List[ast.stmt]) -> Optional[str]:
        for s in stmts:
            if isinstance(s, ast.Return):
                val = cls._extract_constant(s.value)
                return str(val) if val is not None else None
        return None

    @classmethod
    def _extract_constant(cls, node: Optional[ast.expr]) -> Any:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return node.value
        return None

    @classmethod
    def _ast_test_to_condition(cls, test: ast.expr) -> ConditionNode:
        """Converts an AST comparison / boolean test into ConditionNode."""
        # Check Compare: e.g. ticket.get('waiting') >= 30
        if isinstance(test, ast.Compare):
            left_field = cls._resolve_field_name(test.left)
            if not left_field:
                return UnsupportedCondition(raw_text="complex left expression", reason="Cannot resolve field")

            op = test.ops[0]
            comparator = test.comparators[0]
            comp_val = cls._extract_constant(comparator)

            # Check if missing / is None
            if isinstance(op, (ast.Is, ast.IsNot)) and comp_val is None:
                return MissingCondition(field_name=left_field, is_missing=isinstance(op, ast.Is))

            op_map = {
                ast.Eq: "==",
                ast.NotEq: "!=",
                ast.Gt: ">",
                ast.GtE: ">=",
                ast.Lt: "<",
                ast.LtE: "<=",
            }
            op_str = op_map.get(type(op))
            if op_str:
                # If numeric comparison against waiting_time or duration
                val_obj: Union[float, int, str, bool, Duration]
                if isinstance(comp_val, (int, float)):
                    # Check if the field implies minutes
                    if "minute" in left_field.lower() or "waiting" in left_field.lower():
                        val_obj = Duration(value=float(comp_val), unit="minutes")
                    else:
                        val_obj = comp_val
                else:
                    val_obj = comp_val

                return ComparisonCondition(field_name=left_field, operator=op_str, value=val_obj)

            if isinstance(op, (ast.In, ast.NotIn)):
                if isinstance(comparator, (ast.List, ast.Tuple, ast.Set)):
                    values = [cls._extract_constant(el) for el in comparator.elts]
                    return InSetCondition(
                        field_name=left_field,
                        values=tuple(values),
                        negated=isinstance(op, ast.NotIn),
                    )

        # Check BoolOp: AND / OR
        if isinstance(test, ast.BoolOp):
            op_type = "AND" if isinstance(test.op, ast.And) else "OR"
            children = [cls._ast_test_to_condition(v) for v in test.values]
            return LogicalCondition(operator=op_type, children=tuple(children))

        # Check UnaryOp: NOT
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            inner = cls._ast_test_to_condition(test.operand)
            return LogicalCondition(operator="NOT", children=(inner,))

        return UnsupportedCondition(raw_text="unhandled AST test", reason="Complex or unsupported AST condition")

    @classmethod
    def _resolve_field_name(cls, node: ast.expr) -> Optional[str]:
        # ticket.get('field') or ticket['field']
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if node.args and isinstance(node.args[0], ast.Constant):
                    return str(node.args[0].value)
        elif isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant):
                return str(node.slice.value)
        elif isinstance(node, ast.Attribute):
            return node.attr
        elif isinstance(node, ast.Name):
            return node.id
        return None
