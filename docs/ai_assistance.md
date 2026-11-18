# Material AI Assistance Disclosure

**Project:** AI-Assisted Workflow Reviewer (Candidate Assessment 2026)  
**Date:** November 2026  
**Candidate Name:** Assessment Submission  

---

## 1. Scope & Nature of AI Assistance

In accordance with assessment instructions (*"AI tools are permitted; you are responsible for verifying code and results"*), AI assistance (Gemini / Antigravity agentic pair programming) was utilized during the development of this repository.

The material areas of assistance included:
1. **Schema & Boilerplate Generation:** Assisting in scaffolding initial Pydantic models for contracts (`models.py`) and defining standard AST node structures (`conditions.py`).
2. **Synthetic Evaluation Dataset Creation:** Generating diverse permutations of the 16 JSON review bundles in `generate_fixtures.py` (e.g. reordering JSON keys, generating synthetic timestamps, and generating isomorphic rule renamings).
3. **Markdown Documentation Formatting:** Formatting tables, mathematical notation, and structuring the engineering note.

---

## 2. Independent Verification & Correctness Measures

To ensure that AI assistance did not introduce unverified code, circular reasoning, or silent correctness bugs, the following rigorous verification practices were applied:

1. **Independent Ground Truth Oracle:** Expected evaluation labels and counterexample expectations were coded in an independent Python module (`RoutingOracle` in `ground_truth.py`) rather than derived from reviewer outputs.
2. **Zero-Dependency Constraint Solver:** The core interval solver, DNF converter, boundary witness sampler, and counterexample synthesizer were implemented with full mathematical derivation and tested across edge cases (e.g. $[1800, \infty)$ vs $(1800, \infty)$).
3. **Static AST Analysis without Execution:** Code snippet analysis was built using Python's standard `ast` module with an explicit allowlist of safe nodes (`ast.Compare`, `ast.If`, `ast.Return`), preventing any unsafe `eval()` or unverified dynamic execution.
4. **Reproducible Test Suite:** 19 comprehensive unit and regression tests were written using `pytest`, running locally and completing in 0.16 seconds on standard CPU hardware.
5. **Frozen Evaluation Benchmark:** The benchmark was run across three separate conditions (Baseline v0, Controlled Ablation, and Final Reviewer) on a frozen 16-bundle dataset with 4 holdout bundles unseen during tuning.

All code, mathematical logic, and results were verified and validated through local CPU test runs and reproducible offline commands.
