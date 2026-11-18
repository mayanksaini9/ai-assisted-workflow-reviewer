# AI-Assisted Workflow Reviewer

An evidence-backed, multi-stage AI-assisted reviewer that verifies whether structured decision workflows, specifications, and linked Python code implement written policy requirements.

Built for the **SHODH AI & ML Engineer Intern Assessment (2026)**.

---

## Quickstart & Reproducibility

This repository is built from scratch with zero paid APIs, GPU requirements, or external solver dependencies. It executes 100% offline in a reproducible CPU environment.

### 1. Requirements & Setup
Python 3.10+ is required. Pinned dependencies are listed in `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 2. Single Command Offline Evaluation
Run the complete offline benchmark (baseline, final, controlled ablation, and preserved regressions):
```bash
python run_evaluation.py
```
This runs in ~15 milliseconds on a single CPU core and generates machine-readable reports in `reports/`:
- `reports/baseline_evaluation.json`
- `reports/final_evaluation.json`
- `reports/ablation_evaluation.json`

### 3. Run Test Suite
Run the full 19-test automated test suite across all 4 stages:
```bash
python -m pytest
```

---

## CLI Usage

The reviewer includes a full command-line interface:

### Review an Individual Bundle
```bash
# Formatted terminal output
python -m reviewer.cli review fixtures/bundle_01_valid_standard.json

# Machine-readable JSON output
python -m reviewer.cli review fixtures/bundle_08_defect_code_disagreement.json --json
```

### Run Evaluation from CLI
```bash
python -m reviewer.cli evaluate
```

### Run Ablation from CLI
```bash
python -m reviewer.cli ablate
```

---

## Four Cumulative Stages Overview

### Stage 1: Input Contracts & Effective Requirements
- **Pydantic Contracts (`reviewer/contracts/models.py`):** Schemas for anchored source policies, specifications, versioned workflow graphs, linked Python snippets, and top-level approval records.
- **Expressive DSL & AST (`reviewer/contracts/conditions.py`, `parser.py`):** Supports null/missing checks, real numeric intervals with inclusive/exclusive bounds, categorical sets, nested boolean logic (AND/OR/NOT), and time unit normalization (`30 minutes` = `0.5 hours` = `1800 seconds`).
- **Effective Resolver (`reviewer/contracts/effective.py`):** Enforces top-level approval authority, chronological change application, prose approval detection, and contradictory approval conflict detection.

### Stage 2: Semantic Review Across Artifacts
- **Constraint Solver & Counterexamples (`reviewer/semantic/solver.py`):** Zero-dependency interval and categorical solver. When an artifact diverges, synthesizes a concrete ticket counterexample showing expected vs actual routes.
- **Workflow Graph Analyzer (`reviewer/semantic/graph_analyzer.py`):** Analyzes reachability, dead branches, and detects precedence inversions along graph paths.
- **Static AST Code Inspector (`reviewer/semantic/code_analyzer.py`):** Safely parses restricted Python AST without execution (`eval`/`exec` disallowed), catching logic and boundary discrepancies despite matching docstrings.

### Stage 3: Model Integration Trust & Failure Handling
- **Model Client Interface (`reviewer/model/client.py`):** Abstract interface with a reproducible `FakeModelClient` (modes: NORMAL, TIMEOUT, MALFORMED_JSON, HALLUCINATED_RULE_REF, PROMPT_INJECTION_ECHO) and `RealModelClient` stub.
- **Validator & Security (`reviewer/model/validator.py`, `prompts.py`):** Enforces JSON schema validity, catches hallucinated rule references (e.g. R99), isolates prompt injections, and guarantees that deterministic blockers cannot be downgraded to PASS.
- **Caching:** Cache keys include `SHA-256(bundle + approvals + config)` so modified artifacts or approvals immediately invalidate stale passing reports.

### Stage 4: Controlled Evaluation & Debugging
- **16 Frozen Bundles (`fixtures/`):** 4 valid cases, 9 defective cases, and 3 incomplete cases. Includes 4 reserved holdout cases (Bundles 13–16) unseen during tuning.
- **Baseline Checkpoint (v0):** Syntactic reviewer compared directly against the final hybrid reviewer.
- **Controlled Ablation:** Disabling static AST code inspection demonstrates a drop from 100% to 93.75% accuracy (False Pass on Bundle 08).
- **Two Preserved Debugging Examples:** Preserved failing output, diagnosis, focused fix, and regression tests for boundary operator conflation (`>` vs `>=`) and prose approval bypass/conflict blindness.

---

## Evaluation Benchmark Summary

| Metric | Baseline (v0 Checkpoint) | Controlled Ablation (No AST) | Final Hybrid Reviewer |
| :--- | :---: | :---: | :---: |
| **Overall Accuracy** | 56.25% (9/16) | 93.75% (15/16) | **100.0% (16/16)** |
| **False Passes (Defect $\to$ PASS)** | 2 | 1 | **0** |
| **False Blocks (Valid $\to$ BLOCK)** | 2 | 0 | **0** |
| **Incomplete Cases Accurately Flagged** | 0 | 3 | **3** |
| **Blocker Precision / Recall** | 70.0% / 77.8% | 100.0% / 88.9% | **100.0% / 100.0%** |
| **Synthesized Counterexamples** | 0 | 5 | **8 (Oracle-verified)** |
| **Avg Runtime per Case** | 0.11 ms | 0.77 ms | **0.91 ms** |

---

## Documentation Links

- [Engineering Note (4 pages)](docs/engineering_note.md)
- [Material AI Assistance Disclosure](docs/ai_assistance.md)
- [Ground Truth Registry](reviewer/evaluation/ground_truth.py)
- [Preserved Debugging Examples](reviewer/evaluation/regressions.py)
