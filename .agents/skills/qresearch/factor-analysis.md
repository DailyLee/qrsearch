# Factor analysis — frozen market workflow

This phase selects candidate factor references using train-only evidence. It does not design a
strategy, tune thresholds, inspect validate/holdout results for selection, or run qresearch-local
factor formulas.

## Required flow

```text
qr data ping
  → qr research factors
  → qr config new (market template; fill candidate zer0factor refs, universe, and temporal roles)
  → qr research materialize
  → qr research evaluate
  → read zer0factor summary/report/daily_ic/quantile_returns
  → read qrsearch factor_redundancy.parquet
  → write factor_analysis decision from train evidence only
  → stop at the Iteration 3 boundary for strategy design, OOS backtest, and gates
```

Commands:

```bash
qr data ping --format json --quiet
qr research factors --format json --quiet
qr config new --out configs/experiments/<study>.yaml --study-id <study> \
  --set sample.universe=<zer0share_universe> \
  --set sample.start_date=<YYYY-MM-DD> \
  --set sample.end_date=<YYYY-MM-DD> \
  --set 'features.refs=[{name: <registry_name>, availability_lag_sessions: <N>}]' \
  --set 'evaluation.train_years=[<YYYY>]' \
  --set 'evaluation.validate_years=[<YYYY>]' \
  --set 'evaluation.holdouts=[{years: [<YYYY>], role: final}]' \
  --format json --quiet
qr research materialize --config configs/experiments/<study>.yaml --format json --quiet
qr research evaluate --config configs/experiments/<study>.yaml \
  --run-id <materialize_run_id> --format json --quiet
```

`research evaluate` internally calls zer0factor's public `EvaluationService`. It supplies only the
frozen `role=train` membership and reads factor values only from the exact persisted snapshot for
that run. qresearch does not call its deleted IC, quantile, monotonicity, band-IC, diagnostics, or
preprocessing implementations.

## Evidence to read

For every candidate, inspect zer0factor's:

- `summary.csv` or `summary.parquet`;
- `report.md`;
- `daily_ic.parquet`;
- `quantile_returns.parquet`;
- audited `clean_factor_data.parquet` when diagnosing coverage.

Then inspect qresearch's `factor_redundancy.parquet`, which is the sole local supplemental
diagnostic. It contains mean same-session train rank correlation and does not score or remove factors.

The screening manifest records excluded validate/holdout counts, the exact persisted feature snapshot
SHA-256, zer0factor revision, request, and every artifact hash. If an artifact is absent or contains a
date/instrument outside frozen train membership, the run is invalid.

## Selection discipline

- Use only train evidence to choose, reject, or compare factors.
- Do not read validate or holdout outcomes and then change factor selection, signs, horizons, or refs.
- Do not choose a fixed Top-N by rank. Record economic rationale, stability, missingness, quantile
  shape, and redundancy for each accepted/rejected factor.
- Raw-versus-neutralized comparisons use separate raw and neutralized factor refs that already exist
  in zer0factor and are listed together in `features.refs`. qresearch never neutralizes, winsorizes,
  or z-scores them on demand.
- Do not call the removed `qr factor compare`, `qr factor preprocess`, or
  `qr factor band-ic` commands.
- Do not write strategy filters, ranking thresholds, risk values, or execution values in this phase.

## Decision record

After reviewing only train artifacts:

```bash
qr study decision --study <study_id> --stage factor_analysis \
  --summary "candidate refs reviewed; train evidence only" \
  --rationale "economic rationale + zer0factor IC/quantiles + qrsearch redundancy" \
  --evidence '{"run_id":"<run_id>","chosen":[],"rejected":[],"snapshot_sha256":"<sha256>"}' \
  --run <run_id> --next-action "Iteration 3 strategy design" --format json --quiet
```

The chosen/rejected arrays must be filled from the artifacts; the command above is syntax, not a
factor recommendation.
