# Factor analysis — train-only frozen market workflow

Materialize once, then evaluate the same run:

```bash
qr research materialize --config configs/experiments/<study>.yaml --run-id <id> --format json --quiet
qr research evaluate --config configs/experiments/<study>.yaml --run-id <id> --format json --quiet
```

Read zer0factor's `summary`, `report`, `daily_ic`, `quantile_returns`, and qresearch's `factor_redundancy.parquet`. These evidence files are restricted to frozen `role=train` membership. Record accepted and rejected refs, economic rationale, coverage, stability, shape, and redundancy in `qr study decision --stage factor_analysis`.

Do not inspect validate or holdout outcomes to choose factor refs, signs, horizons, preprocessing, or thresholds. qresearch neither calculates a second IC stack nor performs on-demand neutralization/preprocessing: raw and neutralized comparisons must be separate zer0factor refs.

After decision, strategy work remains on this exact run. It cannot rematerialize a new snapshot or tune on non-train roles.
