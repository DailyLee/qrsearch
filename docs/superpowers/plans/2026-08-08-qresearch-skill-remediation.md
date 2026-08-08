# qresearch Skill Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate pipeline sample leakage and stale research instructions.

**Architecture:** Pipeline commands load an existing immutable run and select a declared temporal role before constructing signal inputs. Market sample lineage captures daily ST-filter quality, and promotion rejects incomplete universe filtering. Documentation describes this contract as the only active workflow.

**Tech Stack:** Python 3.12, Pydantic, Polars, Typer, pytest, Markdown.

## Global Constraints

- Market-only zer0share and zer0factor inputs; no CSV/event command surface.
- CLI agent calls use `--format json --quiet`.
- No implicit materialization in pipeline commands.
- Promotion requires full PIT ST-filter lineage.

---

### Task 1: Frozen-run role selection

**Files:** `qresearch/pipeline.py`, `qresearch/research/pipeline.py`, `qresearch/cli.py`, `tests/test_research_pipeline_cli.py`, `tests/test_research_backtest_pipeline.py`.

- [ ] Write tests requiring a supplied existing run, role filtering, train-only search, and metadata evidence.
- [ ] Run the focused tests and observe failure against implicit materialization/full-frame behavior.
- [ ] Add a shared frozen-run loader that validates config equivalence, filters `dataset.frame` by role, rejects empty selections, and returns the selected dataset metadata.
- [ ] Pass `run_id` and `role` through all pipeline Typer commands; restrict search role to train.
- [ ] Run focused tests and then the complete suite.

### Task 2: ST filter lineage and promote gate

**Files:** `qresearch/research/providers/market.py`, `qresearch/research/pipeline.py`, `qresearch/engines/experiment/promote.py`, `tests/test_research_promote.py`, market provider tests.

- [ ] Write tests for status aggregation and forced promotion rejection when status is not full.
- [ ] Run focused tests and observe failure.
- [ ] Read daily zer0share build metadata, persist aggregate status in sample/run lineage, and reject promotion unless it equals full.
- [ ] Run focused tests and complete suite.

### Task 3: Active workflow documentation

**Files:** `.agents/skills/qresearch/*.md`, `README.md`, `qresearch/config/models.py` comment.

- [ ] Replace event/CSV/ops and inactive-iteration language with the frozen-run role contract.
- [ ] State which gates are automated and which are manual review.
- [ ] Search for retired commands and confirm none remain in active Skill/README text.
