# qresearch

Market-universe A-share research kernel and agent-friendly CLI.

Iteration 3 adds market-only strategy research on the frozen dataset: observations are adapted to the
existing daily signal and backtest engines without querying zer0factor again. Event and CSV inputs remain
unsupported.

## Install

```bash
python -m pip install -e ".[dev]"
```

Market data comes only from zer0share `LocalPro`; factor values come only from an existing, read-only
zer0factor `FactorStorage`. qresearch does not depend on vnpy and never creates fallback market or
factor data.

Optional environment variables:

- `ZER0SHARE_ROOT`: zer0share repository used to import `LocalPro`.
- `ZER0SHARE_DATA`: local zer0share parquet root.
- `ZER0FACTOR_ROOT`: zer0factor repository used for public storage/evaluation imports.
- `ZER0FACTOR_FACTOR_DIR`: existing factor partition directory, opened read-only.
- `ZER0FACTOR_DB_PATH`: existing factor registry DuckDB, opened read-only.

## Market factor workflow

Always use `--format json --quiet` for agent calls. stdout is one JSON envelope.

```bash
qr data ping --format json --quiet
qr research factors --format json --quiet

qr config new \
  --out configs/experiments/<study>.yaml \
  --study-id <study> \
  --set sample.universe=<zer0share_universe> \
  --set sample.start_date=<YYYY-MM-DD> \
  --set sample.end_date=<YYYY-MM-DD> \
  --set 'features.refs=[{name: <zer0factor_ref>, availability_lag_sessions: 0}]' \
  --set 'evaluation.train_years=[<YYYY>]' \
  --set 'evaluation.validate_years=[<YYYY>]' \
  --set 'evaluation.holdouts=[{years: [<YYYY>], role: final}]' \
  --format json --quiet

qr research materialize --config configs/experiments/<study>.yaml --format json --quiet
qr research evaluate --config configs/experiments/<study>.yaml --run-id <materialize_run_id> --format json --quiet
qr pipeline research --config configs/experiments/<study>.yaml \
  --run-id <materialize_run_id> --role train --format json --quiet
qr pipeline research --config configs/experiments/<study>.yaml \
  --run-id <materialize_run_id> --role validate --format json --quiet
qr pipeline research --config configs/experiments/<study>.yaml \
  --run-id <materialize_run_id> --role holdout_final --format json --quiet
```

`research factors` only lists readable registry names. It does not read factor values, calculate
formulas, or create a run.

`research materialize` executes one fixed sequence: samples → features → price panel → labels →
dataset → temporal roles. It writes the frozen artifacts below
`workspace/runs/<run_id>/artifacts/`. After `feature_snapshot.parquet` is written, qresearch hashes
those exact persisted bytes; the SHA-256 is copied into the snapshot manifest, nested snapshot meta,
run meta, dataset input lineage, and factor-screening audit.

`research evaluate` reuses the same run's frozen dataset and feature snapshot. With no `--run-id`,
it first materializes a new run and evaluates that run. It sends only `role=train` membership and the
frozen snapshot to zer0factor. validate and holdout rows are excluded from screening and must never be
used to select factors. An explicit `--run-id` must already exist; a missing run is a data error and
is never materialized by `evaluate`.

Every materialized run writes exactly these stable artifacts:

- `sample_set.parquet`
- `feature_snapshot.parquet`
- `feature_manifest.json`
- `label_set.parquet`
- `dataset.parquet`
- `split_summary.json`

Evaluation additionally exposes zer0factor `summary.csv`, `summary.parquet`, `report.md`, metadata,
and each factor's `clean_factor_data.parquet`, `daily_ic.parquet`, and
`quantile_returns.parquet`. qresearch adds only `factor_redundancy.parquet` plus
`factor_screening_manifest.json`; it does not maintain a second IC, quantile, monotonicity, or
preprocessing implementation.

## CLI and exit contract

Current market-research commands:

```text
qr data ping
qr data clear-cache
qr research factors
qr research materialize --config <yaml> [--run-id <id>]
qr research evaluate --config <yaml> [--run-id <id>]
qr config new --out configs/experiments/<name>.yaml --study-id <id> [--set key=value]
qr study decision ...
qr study list ...
```

Every `pipeline` command reuses an existing, evaluated frozen run and requires an explicit temporal
role. `pipeline research` accepts `train`, `validate`, `holdout_final`, or `holdout_stress`; optimize,
sweep, and sensitivity accept only `train`. Pipeline commands validate the supplied YAML against the
run snapshot, never rematerialize, and record the selected role/input row count in run metadata. Set
`risk.max_hold_sessions` explicitly; rows without a calendar exit are omitted rather than assigned a
guessed holding period.

Role-specific backtests are preserved under `artifacts/backtests/<role>/`; use `qr analyze trades --run
<id> --role <role>` or `qr analyze report --run <id> --role <role>` to inspect one role without
overwriting another.

The PIT universe lineage includes `st_filter_status`. A status other than `full` (including
`listed_only`, `mixed`, or `unknown`) is suitable only for explicitly limited research and cannot be
promoted, even with `--force`.

JSON envelopes include `schema_version`, `run_id`, `summary`, `artifacts`, `next_actions`, and
`error`. Exit codes are `0` success, `2` configuration, `3` market/factor coverage or artifact
audit failure, `4` blocked gate, and `5` missing dependency. Missing universe data or configured
factor coverage is an error; materialization never reports a skipped success.

## Example and experiment configs

`configs/examples/market_factors.yaml` is the only example skeleton. Its universe, dates, temporal
roles, and `features.refs` are intentionally incomplete, and its strategy signals are empty. Do not
edit the example into an experiment. Use `qr config new` to write a new file under
`configs/experiments/` and fill only real zer0factor registry references.

## Tests

```bash
python -m pytest -q
```

Synthetic tests cover market membership, persisted snapshot identity, fixed-horizon/PIT labels,
temporal purging, train-only zer0factor screening, historical limits, T+1, and lower-level execution
correctness. Historical files below `workspace/events/**` and `workspace/events_ascii/**` remain
read-only archival data; the product no longer consumes them.

Agent execution guidance is in [`.agents/skills/qresearch/SKILL.md`](.agents/skills/qresearch/SKILL.md).
Engineering changes must keep configuration, tests, skill guidance, and this README aligned as defined
by [`AGENTS.md`](AGENTS.md).
