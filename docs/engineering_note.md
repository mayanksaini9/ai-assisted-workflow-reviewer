# Engineering Note: AI-Assisted Workflow Reviewer

**Author:** Candidate Assessment 2026 (SHODH / AI & ML Engineer Intern Take-Home)  
**Date:** November 2026  
**Stack:** Python 3.12, Pydantic v2, PyYAML, Pytest (Zero external solver / API dependencies)  
**Environment:** Reproducible CPU-only, offline deterministic execution

---

## 1. Executive Summary & Supported Scope

Modern support operations rely on written routing policies enacted through workflow graphs, structured specifications, and Python scripts. However, thresholds, exceptions, and approval status frequently diverge across artifacts. The **AI-Assisted Workflow Reviewer** provides a defensible, multi-stage verification framework that checks whether implemented workflows strictly adhere to authoritative written policies across revisions and conflicting inputs.

### Supported Language & Scope Boundaries
The reviewer formally verifies a well-defined domain:
- **Missing / Null Attributes:** Checked via `missing(field)` or `field is None` / `field is null`.
- **Numeric & Scalar Intervals:** Comparison operators (`>`, `>=`, `<`, `<=`, `==`, `!=`) supporting both inclusive and exclusive bounds.
- **Time Unit Normalization:** Natural time expressions (`30 minutes`, `0.5 hours`, `1800 seconds`, `2 days`) normalized to canonical seconds.
- **Categorical & Finite Domains:** Equality, inequality, and set membership (`tier in ['VIP', 'platinum']`, `not in`).
- **Propositional Logic:** Arbitrary nesting of `AND`, `OR`, and `NOT` using De Morgan's laws and Disjunctive Normal Form (DNF).
- **Precedence & Exceptions:** Ordered evaluation where earlier matching rules take precedence, including overriding exception clauses.
- **Restricted Python AST:** Safe inspection of Python functions restricted to `if/elif/else`, dictionary lookups (`ticket.get()`, `ticket[]`), boolean expressions, comparisons, and literal returns. Arbitrary execution (`eval`, `exec`, external calls, loops) is disallowed.
- **Explicit Visibility of Unsupported Constructs:** Any construct outside this grammar (e.g. subjective natural language like *"customer feels intensely frustrated"*) is flagged as `UnsupportedCondition`. It remains visible in coverage disclosures rather than disappearing from analysis, triggering an `INCOMPLETE` verdict when no explicit blocker exists.

---

## 2. Architecture & Design Choices

```
                                      [ ReviewBundle ]
                                             │
                      ┌──────────────────────┼──────────────────────┐
                      ▼                      ▼                      ▼
             [Source Requirements]    [Specification]     [Workflow Graph & Code]
                      │                      │                      │
                      └──────────────┬───────┴──────────────────────┘
                                     ▼
                    [Effective Requirements Resolver]
                    - Top-level approval authority
                    - Chronological change ordering
                    - Contradiction / conflict blocker
                                     │
                                     ▼
                      [Deterministic Semantic Engine]
                      ├─ DomainSolver (Intervals & DNF)
                      ├─ WorkflowGraphAnalyzer (Paths & Precedence)
                      └─ CodeSnippetAnalyzer (Restricted AST)
                                     │
                        (Counterexample Tickets)
                                     │
                                     ▼
                    [Model Integration & Trust Guard]
                    ├─ FakeModelClient (Offline Reproducible)
                    ├─ Anti-Injection Prompt Isolation
                    ├─ Hallucinated Rule Detector
                    └─ Non-Downgrade Invariant Enforcer
                                     │
                                     ▼
                              [ReviewReport]
                      (PASS | CHANGES_REQUIRED | INCOMPLETE)
```

### Key Architectural Invariants:
1. **Top-Level Approval Authority:** Only top-level signed `ApprovalRecord` entries with `is_top_level=True` and `status="APPROVED"` can modify baseline policies. Unverified claims in prose (e.g., *"Approved by Alice in Slack"*) are flagged as unverified warnings. Contradictory approvals targeting the same rule field with conflicting values trigger an evidenced blocker (`FIND-APPR-CONFLICT`).
2. **Authority Separation:** The deterministic solver is the sole ground-truth authority for formal correctness. A model can enrich explanations, synthesize remediation advice, or surface ambiguities, but **can never downgrade an evidenced blocker to PASS**.
3. **Format Validity $\neq$ Semantic Correctness:** A syntactically valid JSON bundle or code snippet that alters a boundary ($>$ vs $\ge$) or flips precedence is flagged with an evidenced blocker and a concrete counterexample ticket.
4. **Cache Invalidation:** The cache key is derived from the SHA-256 hash of all artifact contents, approval records, and model configurations (`SHA-256(bundle + approvals + config)`). Any artifact alteration invalidates the cache immediately.

---

## 3. Semantic Checks & Counterexample Synthesis

### 3.1 Interval Arithmetic & Equivalence Proofs
Conditions are converted into Disjunctive Normal Form (DNF) represented as sets of `TicketDomainState` constraints. For numeric attributes (e.g., `waiting_time`), we implement `NumericInterval` with explicit `min_inclusive` and `max_inclusive` flags:
$$\text{Interval}_1 = [1800, \infty) \quad \text{vs} \quad \text{Interval}_2 = (1800, \infty)$$
Equivalence holds if and only if $\text{Cond}_1 \implies \text{Cond}_2$ and $\text{Cond}_2 \implies \text{Cond}_1$. To prove divergence, the solver queries:
$$\text{Counterexample} = \text{Satisfy}(\text{Cond}_1 \land \neg \text{Cond}_2)$$
For the boundary mismatch above, the solver samples the boundary witness point $w = 1800.0$ seconds (30 minutes), generating a ticket that satisfies the effective requirement but fails the specification or code.

### 3.2 Precedence Inversion & Interaction Detection
When rules interact (e.g. $R_1$: Missing category $\to$ `manual_review` vs $R_2$: Urgent $\to$ `priority_queue`), the solver evaluates whether an input satisfies $R_1 \land R_2$. If the workflow graph or Python snippet evaluates $R_2$ before $R_1$, the ticket:
$$\text{Ticket} = \{\text{category}: \text{None}, \text{urgency}: \text{'urgent'}\}$$
diverges by routing to `priority_queue` instead of `manual_review`.

### 3.3 Static AST Code Inspection
Rather than executing untrusted code, `RestrictedPythonASTAnalyzer` parses the snippet AST, validates structural safety, extracts conditional branches $(C_i, \text{action}_i)$, and verifies semantic equivalence against effective requirements.

---

## 4. Controlled Evaluation & Statistical Metrics

Evaluation was conducted across **16 frozen review bundles** established before tuning, with expected outcomes verified by an independent routing oracle (`RoutingOracle`). Four bundles (Bundles 13–16) were reserved as a holdout evaluation set.

### 4.1 Comparative Benchmark Results

| Metric | Baseline Reviewer (v0) | Controlled Ablation (No AST) | Final Hybrid Reviewer |
| :--- | :---: | :---: | :---: |
| **Overall Accuracy** | **56.25%** (9/16) | **93.75%** (15/16) | **100.0%** (16/16) |
| **False Passes (Defect $\to$ PASS)** | 2 | 1 | **0** |
| **False Blocks (Valid $\to$ BLOCK)** | 2 | 0 | **0** |
| **Incomplete Verdicts Accurately Identified** | 0 | 3 | **3** |
| **Blocker Precision** | 70.0% | 100.0% | **100.0%** |
| **Blocker Recall** | 77.78% | 88.89% | **100.0%** |
| **Counterexamples Synthesized** | 0 | 5 | **8** (Oracle-verified) |
| **Avg Runtime per Case** | 0.11 ms | 0.77 ms | **0.91 ms** |
| **Hardware Environment** | CPU-only | CPU-only | CPU-only |

### 4.2 Analysis of Results & Ablation Finding
- **Baseline v0 Failures:** Baseline v0 relied on literal string matching without unit conversion or AST analysis. It caused **False Blocks** on valid bundles (e.g. Bundle 03 using `0.5 hours`, Bundle 16 using renamed IDs) and **False Passes** on subtle code defects (Bundle 08) and approval conflicts (Bundle 05).
- **Controlled Ablation:** Disabling static AST code inspection while holding all settings and inputs identical resulted in a **False Pass on Bundle 08** (defect involving docstring claiming $w \ge 30$ while code executed $w > 30$). Blocker recall dropped from **100.0% to 88.89%**, demonstrating that docstring/metadata inspection alone is insufficient.
- **Reserved Holdout Set Performance:** On the 4 holdout bundles (Bundles 13–16), the Final Reviewer achieved 100% accuracy, catching exclusive boundary errors, 60x unit discrepancies, unauthorized threshold changes, and verifying isomorphic renamings.

---

## 5. Preserved Substantive Debugging Examples

### Debugging Example 1: Boundary Inclusive vs Exclusive Conflation
- **Fault Label:** `[DELIBERATE FAULT INJECTION 1: BOUNDARY_OPERATOR_CONFLATION]`
- **Actual Failing Output:**
  ```
  AssertionError: assert True is False
  where True = DomainSolver.check_equivalence(c_inclusive, c_strict)[0]
  Note: Failed to generate boundary counterexample at waiting=30.0
  ```
- **Diagnosis:** The interval solver treated strict inequality ($>$) and non-strict inequality ($\ge$) as equivalent overlapping real sets because `min_inclusive` was omitted during equivalence checks, and witness sampling picked midpoint values rather than boundary extrema.
- **Focused Fix:** Enhanced `NumericInterval` with explicit `min_inclusive` and `max_inclusive` flags; updated `sample_witness()` to test the exact boundary point $x = \text{min\_val}$ whenever inclusiveness differed.
- **Regression Check:** `test_regression_boundary_exclusive()` in `tests/test_debugging_regressions.py` verifies boundary synthesis at $w = 1800.0$s.

### Debugging Example 2: Prose Approval Injection & Conflict Blindness
- **Fault Label:** `[DELIBERATE FAULT INJECTION 2: PROSE_APPROVAL_BYPASS_AND_CONFLICT_BLINDNESS]`
- **Actual Failing Output:**
  ```
  AssertionError: assert <ReviewStatus.PASS> == <ReviewStatus.CHANGES_REQUIRED>
  Note: Reviewer silently selected CR-101 (25m) and ignored CR-102 (20m) without raising a conflict blocker!
  ```
- **Diagnosis:** The approval resolver naively accepted any string mentioning "approved" and took the first matching record in iteration order without grouping by `(rule_ref, field)` to detect contradictory directives.
- **Focused Fix:** 
  1. Mandated `is_top_level=True` on all approval records.
  2. Implemented `_check_prose_approval_claims()` to flag unauthorized claims in prose text.
  3. Added contradiction detection grouping records by `(rule_ref, field)` and emitting `FIND-APPR-CONFLICT` blocker on conflicting approved values.
- **Regression Check:** `test_regression_prose_approval_and_conflict()` in `tests/test_debugging_regressions.py`.

---

## 6. Limitations & Soundness Boundaries

1. **Restricted Domain Completeness:** Deterministic equivalence checking is complete and exhaustive over linear real intervals and finite categorical sets. For arbitrary non-linear arithmetic or unrestricted external Python libraries, the reviewer explicitly abstains, surfaces the construct in coverage disclosures, and returns `INCOMPLETE`.
2. **Offline Model Simulation:** In accordance with the take-home instructions, model evaluations were conducted using a reproducible deterministic `FakeModelClient`. When connecting real LLM clients, network latency and token budgets must be bounded using the provided `ModelConfig`.
