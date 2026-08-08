---
name: qresearch
description: 编排 qrsearch 的市场因子物化与 train-only zer0factor 评估；用户提到 qr、因子分析、market research 或冻结因子快照时使用。
---

# qresearch — Iteration 3 market research workflow

This iteration exposes deterministic market materialization, train-only factor screening, and the
market-only `qr pipeline research` strategy/backtest path. It always reads the frozen run dataset after
materialization; it never queries zer0factor during signal generation or backtesting.

## Required workflow

1. Read [reference.md](reference.md) for the active CLI and artifact contract.
2. Follow [factor-analysis.md](factor-analysis.md) exactly.
3. Use `qr data ping --format json --quiet`.
4. List registry names with `qr research factors --format json --quiet`.
5. Create a new experiment from `configs/examples/market_factors.yaml` with `qr config new`; fill
   the real zer0share universe, dates, explicit temporal roles, and existing zer0factor refs.
6. Materialize, then evaluate the same run.
7. Read zer0factor summary/report/daily IC/quantile returns and qresearch factor redundancy.
8. Write the `factor_analysis` decision from train evidence only.
9. Set an explicit `risk.max_hold_sessions`, then run `qr pipeline research --config <yaml> --format json --quiet`.

## Hard constraints

- Agent I/O is `--format json --quiet`; stdout must be one JSON envelope.
- No event/CSV provider or command exists. Historical `workspace/events/**` and
  `workspace/events_ascii/**` remain read-only archival data.
- Market membership comes from daily zer0share universe snapshots, never the current listing table.
- Factor values and evaluation come from zer0factor. qresearch does not calculate a second IC,
  quantile-return, monotonicity, or preprocessing stack.
- The exact persisted `feature_snapshot.parquet` SHA-256 is the run identity used by dataset lineage
  and screening audit.
- Screening sees train membership only. Never use validate/holdout outcomes to select factors.
- Raw and neutralized comparisons require separate, already-materialized zer0factor refs.
- Empty universe, zero factor coverage, missing labels, or incomplete artifacts are failures, never
  skipped successes.

The older strategy/backtest documents in this directory describe future Iteration 3 concerns, not
active commands. Do not execute command snippets from them until the CLI is restored on the frozen
market dataset.
