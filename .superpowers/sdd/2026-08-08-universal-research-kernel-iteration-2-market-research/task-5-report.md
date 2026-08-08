# Task 5 Report — Train-Only zer0factor Evaluation Boundary

## Delivered

- Added explicit year-based temporal role assignment for `train`, `validate`, `holdout_final`, and
  `holdout_stress`, including label-overlap purge at the first non-train observation and
  `purged_train_count` / role-count metadata.
- Added `FrozenSnapshotStorage`, which serves only the in-memory immutable feature snapshot, maps it
  to zer0factor's public storage shape, filters requested dates, and rejects unknown factors,
  duplicate factor keys, manifest disagreement, or post-hash frame mutation.
- Added `TrainUniversePro`, which derives historical membership exclusively from `role=train` rows;
  only `pro_bar` and `index_daily` are delegated to the configured LocalPro.
- Added `run_factor_screening()` through the public `EvaluationService` and `EvaluationRequest`
  imports. The request is fixed to explicit factors, `open_t1`, configured IC periods/universe/
  benchmark, report generation, and `workers=1`.
- Added strict artifact validation for summary, metadata, report, and every factor's
  `clean_factor_data`, `daily_ic`, and `quantile_returns`. Every clean date/asset key is checked
  against frozen train membership.
- Added the sole local supplemental statistic: same-session cross-sectional rank correlation by
  factor pair, averaged over train dates only. It performs no IC, return, quantile, direction,
  scoring, or automatic factor selection.
- Added `FactorScreeningResult`, `factor_screening_manifest.json`, and
  `factor_redundancy.parquet`. The manifest records the request, input and artifact hashes,
  snapshot digest, zer0factor revision, excluded non-train counts, and explicitly marks the
  evidence `oos=false` / `promotable=false`.

## TDD evidence

Observed RED before production implementation:

1. `tests/test_research_splits.py` failed collection with
   `ModuleNotFoundError: qresearch.research.splits`.
2. `tests/test_zer0factor_evaluation_provider.py` failed collection with
   `ModuleNotFoundError: qresearch.research.providers.zer0factor_evaluation`.
3. `tests/test_factor_redundancy.py` failed collection with
   `ModuleNotFoundError: qresearch.research.redundancy`.
4. The public-service test failed collection because `run_factor_screening` did not exist.
5. After the basic adapter was green, the post-hash frame-mutation regression failed with
   `DID NOT RAISE ResearchDataError`; content-hash recomputation made it green.
6. Independent review drove three additional RED cases: evaluator revision mismatch and foreign
   preloaded public modules both failed with `DID NOT RAISE`, while non-object metadata leaked raw
   `AttributeError`. Configured-root module identity checks, evaluator/snapshot revision comparison,
   and JSON object validation made all three green.

## Verification

Required focused command:

`python -m pytest tests/test_research_splits.py tests/test_zer0factor_evaluation_provider.py tests/test_factor_redundancy.py -q`

Result: `17 passed, 1 warning`.

Broader Iteration 2 regression command:

`python -m pytest tests/test_research_domain.py tests/test_market_sample_provider.py tests/test_zer0factor_feature_provider.py tests/test_market_labels.py tests/test_research_dataset.py tests/test_pit_qfq.py tests/test_research_splits.py tests/test_zer0factor_evaluation_provider.py tests/test_factor_redundancy.py -q`

Result: `59 passed, 6 warnings`.

`python -m compileall -q qresearch/research tests/test_research_splits.py tests/test_zer0factor_evaluation_provider.py tests/test_factor_redundancy.py` completed with exit code 0.

## Repository-wide status and external dependency note

The repository-wide `python -m pytest -q` is not collectible because four legacy test modules still
import contracts intentionally removed by earlier universal-kernel tasks: `FactorsConfig`,
`FactorPreprocessConfig`, and `IngestConfig`. Task 5 did not restore compatibility aliases.

The current Python environment also lacks zer0factor's transitive `pyfolio` dependency. Production
imports correctly map this condition to `Zer0FactorDependencyError` (exit-5 boundary); unit tests
exercise the public API contract with public-module test doubles and never substitute a local factor
calculation.

No source or data in zer0factor or zer0share was modified. No event read-only area was modified.

## Independent review

The first read-only review found no Critical issues and three Important provenance/error-classification
gaps. All three were fixed with observed RED/GREEN tests: the loaded public modules must resolve below
the configured zer0factor root, the evaluator git revision must match the snapshot producer revision
and both are recorded, and malformed metadata shapes map to `ResearchDataError`.
