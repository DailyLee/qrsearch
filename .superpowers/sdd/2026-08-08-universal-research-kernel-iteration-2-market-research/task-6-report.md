# Task 6 Report — Market Research CLI and CSV/Event Retirement

## Delivered

- Added the linear `research factors`, `research materialize`, and `research evaluate` market workflow.
- Retired the active CSV/event, local factor-analysis, strategy/backtest, rolling-validation, and ops surfaces required by the Iteration 2 boundary.
- Kept the market example skeleton signal-free and aligned the README and qresearch skill guidance with the active CLI.
- Fixed the final artifact contract so materialization writes exactly:
  `sample_set.parquet`, `feature_snapshot.parquet`, `feature_manifest.json`,
  `label_set.parquet`, `dataset.parquet`, and `split_summary.json` under `artifacts/`.
- Fixed explicit evaluation reuse: `evaluate --run-id <id>` now returns a data error when the run is missing and never materializes that ID. Evaluation without `--run-id` remains the only evaluate path that may materialize a new run.

## TDD evidence for final review fixes

The two new behavioral regressions were run before implementation and both failed for the intended reasons:

- The artifact contract test observed the old eight-file naming surface and old artifact keys.
- The missing explicit run test reached `MarketSampleProvider`, proving that evaluate attempted to rematerialize; it returned exit 1 instead of the required data-error exit 3.

After the minimal pipeline changes, the same two tests passed (`2 passed`).

## Verification

`python -m pytest tests/test_market_research_cli.py tests/test_no_csv_surface.py tests/test_market_sample_provider.py tests/test_zer0factor_feature_provider.py tests/test_market_labels.py tests/test_research_dataset.py tests/test_research_splits.py tests/test_zer0factor_evaluation_provider.py tests/test_factor_redundancy.py -q`

Result: `55 passed, 1 warning`.

`python -m pytest -q`

Result: `179 passed, 6 warnings`.

All warnings are third-party deprecations. No historical event source data was modified.
