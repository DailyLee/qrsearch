# qresearch Iteration 3 CLI reference

This reference describes the active market-factor surface. Event/CSV, local factor analysis, strategy,
optimization, backtest, rolling validation, and ops commands are not available in Iteration 2.

## Required agent path

```text
qr data ping
  → qr research factors
  → qr config new (market skeleton; fill real zer0factor refs, universe, and temporal roles)
  → qr research materialize
  → qr research evaluate
  → read zer0factor summary/report/daily_ic/quantile_returns
  → read qrsearch factor_redundancy.parquet
  → write the factor_analysis decision from train evidence only
  → enter Iteration 3 strategy design, OOS backtest, and gates
```

Use `--format json --quiet` on every agent call. stdout is exactly one JSON envelope.

## Commands

```bash
qr data ping --format json --quiet
qr research factors --format json --quiet
qr research materialize --config <experiment.yaml> [--run-id <id>] --format json --quiet
qr research evaluate --config <experiment.yaml> [--run-id <id>] --format json --quiet
qr pipeline research --config <experiment.yaml> [--run-id <id>] --format json --quiet
qr pipeline optimize --config <experiment.yaml> ... --format json --quiet
qr pipeline sweep --config <experiment.yaml> --set <spec> --format json --quiet
qr pipeline sensitivity --config <experiment.yaml> ... --format json --quiet
```

- `research factors` calls `FactorStorage.list_factors()`, returns sorted readable names, does not
  read values, scan formulas, calculate factors, or create a run.
- `research materialize` executes samples → features → price panel → labels → dataset → roles.
- `research evaluate` uses the frozen dataset and feature parquet from the same run, then calls the
  public zer0factor `EvaluationService` with train membership only.
- An explicit evaluate `--run-id` reuses that materialized run and fails with exit 3 if the run is
  missing; it never materializes that requested ID. Without `--run-id`, evaluate materializes and
  evaluates one new run.
- A configured factor with zero coverage, an empty universe, unusable label coverage, or incomplete
  zer0factor artifacts fails with exit 3. No skip-success fallback exists.

## Configuration skeleton

The only example is `configs/examples/market_factors.yaml`. Its `features.refs` list and strategy
signals are deliberately empty. Create a real config under `configs/experiments/`:

```bash
qr config new \
  --out configs/experiments/<study>.yaml \
  --study-id <study> \
  --set sample.universe=<zer0share_universe> \
  --set sample.start_date=<YYYY-MM-DD> \
  --set sample.end_date=<YYYY-MM-DD> \
  --set 'features.refs=[{name: <registry_name>, availability_lag_sessions: <N>}]' \
  --set 'evaluation.train_years=[<YYYY>]' \
  --set 'evaluation.validate_years=[<YYYY>]' \
  --set 'evaluation.holdouts=[{years: [<YYYY>], role: final}]' \
  --format json --quiet
```

All factor refs must already be materialized in zer0factor. `availability_lag_sessions` is the only
feature-availability shift; values join only when their availability session exactly equals
`asof_session`, with no stale-value forward fill.

## Artifacts and hashes

A materialized run contains:

```text
workspace/runs/<run_id>/
  config.snapshot.yaml
  meta.json
  artifacts/
    sample_set.parquet
    feature_snapshot.parquet
    feature_manifest.json
    label_set.parquet
    dataset.parquet
    split_summary.json
```

The snapshot SHA-256 is calculated from the persisted `feature_snapshot.parquet` file, then recorded
in the snapshot manifest, nested snapshot meta, run meta, dataset `input_hashes.features`, and
screening manifest. Evaluation validates the exact file bytes and the loaded frame before use.

Evaluation adds:

```text
artifacts/
  factor_screening_manifest.json
  factor_redundancy.parquet
  zer0factor_evaluation/<run_id>_train/
    summary.csv
    summary.parquet
    metadata.json
    report.md
    factors/<factor>/
      clean_factor_data.parquet
      daily_ic.parquet
      quantile_returns.parquet
```

Every exposed zer0factor artifact path and SHA-256 is included in
`factor_screening_manifest.json`. `clean_factor_data` keys are audited against frozen train
membership.

## Exit codes

- `0`: succeeded.
- `2`: invalid/mismatched configuration.
- `3`: missing data, zero coverage, invalid frozen input, or incomplete/audit-failing artifacts.
- `4`: blocked gate for retained utilities.
- `5`: zer0share or zer0factor dependency unavailable.

The removed commands must not be called: `qr factor compare`, `qr factor preprocess`,
`qr factor band-ic`, the old pipeline commands, backtest, rolling validate, or ops. qresearch does
not perform local IC, quantile-return, monotonicity, or factor preprocessing in this iteration.
