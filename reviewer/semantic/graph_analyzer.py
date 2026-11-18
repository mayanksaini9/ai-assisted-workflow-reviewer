"""Workflow decision graph analyzer: reachability, path extraction, precedence, and destination verification."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from reviewer.contracts.conditions import (
    ConditionNode,
    LogicalCondition,
    TrueCondition,
)
from reviewer.contracts.effective import EffectiveRequirement
from reviewer.contracts.models import (
    Counterexample,
    EvidenceLocation,
    ReviewFinding,
    Severity,
    WorkflowGraph,
    WorkflowNode,
)
from reviewer.contracts.parser import DSLParser, parse_condition_dict
from reviewer.semantic.solver import DomainSolver


@dataclass
class WorkflowPath:
    """A path through the workflow graph from entry to an action destination."""
    node_sequence: List[str]
    conditions: List[ConditionNode]
    destination_action: str
    destination_node_id: str
    rule_refs_encountered: List[str]


class WorkflowGraphAnalyzer:
    """Analyzes workflow graphs for reachability, dead branches, precedence, and route correctness."""

    @classmethod
    def analyze(
        cls,
        graph: WorkflowGraph,
        effective_requirements: List[EffectiveRequirement],
    ) -> Tuple[List[ReviewFinding], List[WorkflowPath]]:
        findings: List[ReviewFinding] = []

        # 1. Structural validation
        if graph.entry_node_id not in graph.nodes:
            findings.append(
                ReviewFinding(
                    finding_id="FIND-GRAPH-NO-ENTRY",
                    severity=Severity.BLOCKER,
                    artifact="workflow",
                    version=graph.version,
                    location=EvidenceLocation(file_path="workflow.json", node_id=graph.entry_node_id),
                    evidence=f"entry_node_id '{graph.entry_node_id}' missing from nodes",
                    message=f"Workflow graph entry node '{graph.entry_node_id}' does not exist in graph definition",
                    correction="Define valid entry node in workflow graph",
                )
            )
            return findings, []

        # 2. Check reachable nodes and detect unreachable nodes or missing evidence
        reachable_nodes: Set[str] = set()
        cls._mark_reachable(graph.entry_node_id, graph.nodes, reachable_nodes)

        for node_id, node in graph.nodes.items():
            # Check for missing evidence location
            loc = node.location
            if loc is None or (not loc.file_path and not loc.node_id and loc.line_number is None):
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-GRAPH-MISSING-EVIDENCE-{node_id}",
                        severity=Severity.WARNING,
                        rule_ref=node.rule_ref,
                        artifact="workflow",
                        version=graph.version,
                        location=EvidenceLocation(file_path="workflow.json", node_id=node_id),
                        evidence=f"Node '{node_id}' lacks required evidence location anchor",
                        message=f"Missing evidence location anchor on workflow node '{node_id}'",
                        correction="Supply file_path or line_number evidence location for workflow node",
                    )
                )

            if node_id not in reachable_nodes:
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-GRAPH-UNREACHABLE-{node_id}",
                        severity=Severity.WARNING,
                        rule_ref=node.rule_ref,
                        artifact="workflow",
                        version=graph.version,
                        location=node.location or EvidenceLocation(file_path="workflow.json", node_id=node_id),
                        evidence=f"Node '{node_id}' (type={node.node_type}) is unreachable from entry",
                        message=f"Unreachable dead workflow node detected: '{node_id}'",
                        correction="Connect or remove unreachable node from workflow definition",
                    )
                )

        # 3. Extract all valid paths from entry to terminal action nodes
        paths: List[WorkflowPath] = []
        cls._extract_paths(
            current_node_id=graph.entry_node_id,
            nodes=graph.nodes,
            current_path=[],
            current_conds=[],
            current_rule_refs=[],
            paths=paths,
            visited=set(),
        )

        # 4. Check precedence and route correctness against effective requirements
        cls._verify_effective_requirements(graph, paths, effective_requirements, findings)

        return findings, paths

    @classmethod
    def _mark_reachable(cls, current: str, nodes: Dict[str, WorkflowNode], reachable: Set[str]) -> None:
        if current in reachable or current not in nodes:
            return
        reachable.add(current)
        node = nodes[current]
        for branch in node.branches:
            cls._mark_reachable(branch.target_node_id, nodes, reachable)

    @classmethod
    def _extract_paths(
        cls,
        current_node_id: str,
        nodes: Dict[str, WorkflowNode],
        current_path: List[str],
        current_conds: List[ConditionNode],
        current_rule_refs: List[str],
        paths: List[WorkflowPath],
        visited: Set[str],
    ) -> None:
        if current_node_id not in nodes:
            return
        if current_node_id in visited:
            # Cycle detected
            return

        node = nodes[current_node_id]
        new_path = current_path + [current_node_id]
        new_refs = list(current_rule_refs)
        if node.rule_ref:
            new_refs.append(node.rule_ref)

        if node.node_type in ("action", "exit") and node.action:
            paths.append(
                WorkflowPath(
                    node_sequence=new_path,
                    conditions=list(current_conds),
                    destination_action=node.action,
                    destination_node_id=current_node_id,
                    rule_refs_encountered=new_refs,
                )
            )
            return

        visited.add(current_node_id)
        for branch in node.branches:
            b_cond: ConditionNode
            if branch.condition:
                b_cond = parse_condition_dict(branch.condition)
            elif branch.condition_text:
                b_cond = DSLParser.parse(branch.condition_text)
            else:
                b_cond = TrueCondition()

            cls._extract_paths(
                current_node_id=branch.target_node_id,
                nodes=nodes,
                current_path=new_path,
                current_conds=current_conds + [b_cond],
                current_rule_refs=new_refs,
                paths=paths,
                visited=set(visited),
            )

    @classmethod
    def _verify_effective_requirements(
        cls,
        graph: WorkflowGraph,
        paths: List[WorkflowPath],
        effective_requirements: List[EffectiveRequirement],
        findings: List[ReviewFinding],
    ) -> None:
        """Verifies graph paths against effective requirements."""
        # Find which nodes evaluate which rule references
        rule_to_nodes: Dict[str, List[WorkflowNode]] = {}
        for n in graph.nodes.values():
            if n.rule_ref:
                rule_to_nodes.setdefault(n.rule_ref, []).append(n)
            elif n.node_type in ("action", "exit") and n.action:
                # Map action node to fallback rule if condition is TrueCondition and action matches
                for eff in effective_requirements:
                    if isinstance(eff.condition, TrueCondition) and eff.action == n.action:
                        rule_to_nodes.setdefault(eff.rule_id, []).append(n)

        # Precedence check: order of effective requirements
        for i in range(len(effective_requirements) - 1):
            r1 = effective_requirements[i]
            r2 = effective_requirements[i + 1]

            # If both rules have decision nodes in graph
            nodes_r1 = rule_to_nodes.get(r1.rule_id, [])
            nodes_r2 = rule_to_nodes.get(r2.rule_id, [])

            if nodes_r1 and nodes_r2:
                # Check if r2 appears before r1 along any reachable path
                for p in paths:
                    if r2.rule_id in p.rule_refs_encountered and r1.rule_id not in p.rule_refs_encountered:
                        # Check if a ticket satisfying both r1 and r2 would take this path
                        ce_ticket = DomainSolver.generate_precedence_counterexample(r1.condition, r2.condition)
                        if ce_ticket:
                            # Evaluate ticket through graph
                            actual_route = cls.route_ticket(graph, ce_ticket)
                            if actual_route != r1.action:
                                findings.append(
                                    ReviewFinding(
                                        finding_id=f"FIND-GRAPH-PRECEDENCE-{r1.rule_id}-{r2.rule_id}",
                                        severity=Severity.BLOCKER,
                                        rule_ref=r1.rule_id,
                                        artifact="workflow",
                                        version=graph.version,
                                        location=nodes_r2[0].location or EvidenceLocation(file_path="workflow.json", node_id=nodes_r2[0].node_id),
                                        evidence=f"Rule {r2.rule_id} evaluated before {r1.rule_id} along path {p.node_sequence}",
                                        message=f"Precedence inversion in workflow graph: {r2.rule_id} takes precedence over {r1.rule_id}",
                                        correction=f"Reorder workflow nodes so {r1.rule_id} is evaluated before {r2.rule_id}",
                                        counterexample=Counterexample(
                                            ticket=ce_ticket,
                                            expected_route=r1.action,
                                            actual_route=actual_route,
                                            divergence_reason=f"Precedence inversion: {r2.rule_id} triggered before {r1.rule_id}",
                                            evidence={
                                                "source": f"{r1.rule_id} (precedence {r1.precedence}) -> {r1.action}",
                                                "workflow": f"Node {nodes_r2[0].node_id} routed to {actual_route}",
                                            },
                                        ),
                                    )
                                )
                                break

        # Route correctness check for each effective rule
        for eff in effective_requirements:
            nodes_for_rule = rule_to_nodes.get(eff.rule_id, [])
            if not nodes_for_rule:
                # If rule has no corresponding node in graph
                findings.append(
                    ReviewFinding(
                        finding_id=f"FIND-GRAPH-MISSING-RULE-{eff.rule_id}",
                        severity=Severity.BLOCKER,
                        rule_ref=eff.rule_id,
                        artifact="workflow",
                        version=graph.version,
                        location=eff.source_anchor,
                        evidence=f"Effective rule {eff.rule_id}: {eff.original_text}",
                        message=f"Workflow graph completely omits required rule '{eff.rule_id}'",
                        correction=f"Add decision node and branches implementing {eff.rule_id} in workflow graph",
                    )
                )
                continue

            for node in nodes_for_rule:
                # Check branches from this node
                for branch in node.branches:
                    target_node = graph.nodes.get(branch.target_node_id)
                    if target_node and target_node.node_type in ("action", "exit") and target_node.action:
                        # Check branch condition
                        b_cond: ConditionNode
                        if branch.condition:
                            b_cond = parse_condition_dict(branch.condition)
                        elif branch.condition_text:
                            b_cond = DSLParser.parse(branch.condition_text)
                        else:
                            b_cond = TrueCondition()

                        # If branch condition matches effective requirement condition, target action must match
                        is_equiv, _ = DomainSolver.check_equivalence(b_cond, eff.condition)
                        if is_equiv and target_node.action != eff.action:
                            ce = DomainSolver.generate_counterexample(eff.condition, TrueCondition()) or {
                                "category": "billing",
                                "urgency": "urgent",
                                "waiting_time": 35,
                            }
                            findings.append(
                                ReviewFinding(
                                    finding_id=f"FIND-GRAPH-WRONG-DEST-{node.node_id}-{branch.branch_id}",
                                    severity=Severity.BLOCKER,
                                    rule_ref=eff.rule_id,
                                    artifact="workflow",
                                    version=graph.version,
                                    location=node.location or EvidenceLocation(file_path="workflow.json", node_id=node.node_id),
                                    evidence=f"Branch {branch.branch_id} routes to action '{target_node.action}' instead of '{eff.action}'",
                                    message=f"Wrong branch destination for rule {eff.rule_id}: expected '{eff.action}', got '{target_node.action}'",
                                    correction=f"Update branch destination or action node to '{eff.action}'",
                                    counterexample=Counterexample(
                                        ticket=ce,
                                        expected_route=eff.action,
                                        actual_route=target_node.action,
                                        divergence_reason=f"Branch target node {target_node.node_id} has wrong action",
                                        evidence={
                                            "source": f"{eff.rule_id} -> {eff.action}",
                                            "workflow": f"{branch.branch_id} -> {target_node.action}",
                                        },
                                    ),
                                )
                            )

    @classmethod
    def route_ticket(cls, graph: WorkflowGraph, ticket: Dict[str, Any]) -> str:
        """Simulates routing a ticket deterministically through the workflow graph."""
        current_id = graph.entry_node_id
        visited = set()

        while current_id in graph.nodes and current_id not in visited:
            visited.add(current_id)
            node = graph.nodes[current_id]
            if node.node_type in ("action", "exit") and node.action:
                return node.action

            # Decision node
            next_id = None
            for branch in node.branches:
                b_cond: ConditionNode
                if branch.condition:
                    b_cond = parse_condition_dict(branch.condition)
                elif branch.condition_text:
                    b_cond = DSLParser.parse(branch.condition_text)
                else:
                    b_cond = TrueCondition()

                if b_cond.evaluate(ticket):
                    next_id = branch.target_node_id
                    break

            if next_id is None:
                break
            current_id = next_id

        return "unrouted"
