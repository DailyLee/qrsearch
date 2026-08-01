# qresearch CLI & layout reference

Agent research loop lives in [SKILL.md](SKILL.md); phases: [factor-analysis.md](factor-analysis.md) → [strategy-design.md](strategy-design.md) → [backtest-optimize.md](backtest-optimize.md); index [research-loop.md](research-loop.md). This file is CLI/layout only.

## Prices / 前复权

zer0share has **no** as-of qfq API (`pro_bar(adj=qfq)` is window-end). qresearch preloads raw `daily` + `adj_factor` once per study, then applies PIT qfq at read time: `price[t|T] = raw[t] * adj[t] / adj[T]` (`PricePanel.get(..., asof=T)`). Cache prefix `pit_raw_v1_*` — delete old `workspace/cache/prices` if stale.

## Layout

```
qresearch/              # package + CLI
configs/examples/       # templates (copy before editing)
configs/experiments/    # agent-written hypothesis configs
tests/fixtures/         # synthetic panels for unit tests
workspace/              # local inputs + artifacts (gitignored)
  events/               # event CSVs (READ-ONLY for agents; also events_ascii/)
  runs/                 # immutable experiment runs (id = local YYYYMMDD_HHMMSS_mmm)
  studies/              # decision logs per study
  models/               # promoted model packages
  cache/                # price panel cache
```

Run layout:

```
workspace/runs/<run_id>/{meta.json,config.snapshot.yaml,artifacts/,report/}
workspace/studies/<study_id>/{INDEX.md,decisions/*.md|*.json}
workspace/models/<model_id>/<version>/{spec.yaml,provenance.json,metrics_oos.json,report/}
```

Research artifacts (typical): `sample_profile.json`, `ic_summary.csv`, `icir_summary.csv`, `alpha_beta_summary.csv`, `quantile_returns.csv`, `preprocess_report.json`, `events_preprocessed.parquet`, `sensitivity_grid.csv`.

## CLI map

| Command | Purpose |
|---------|---------|
| `qr version` | Package version |
| `qr data ping` | zer0share LocalPro import/path check |
| `qr data validate-events --csv ... [--config ...]` | Ingest mapping / schema check |
| `qr data clear-cache` | Drop `workspace/cache` price panels |
| `qr pipeline research --csv ... [--config ...] [--run-id ...] [--n-trials-assumed N]` | Full research run (+ sample profile, IC/ICIR/quantiles, gates) |
| `qr pipeline optimize --csv ... [--config ...] [--n-trials N] [--feature ...]` | Optuna + WF |
| `qr pipeline sensitivity --csv ... [--cost-mult ...] [--stop ...] [--take ...] [--max-grid N]` | Cost/stop/take grid (no promote) |
| `qr validate rolling --csv ... [--config ...]` | Walk-forward only |
| `qr factor ic --csv ... [--feature ...]` | Event IC for one feature |
| `qr factor preprocess --csv ...` | Winsorize / industry / size / z-score → `__prep` cols |
| `qr factor compare --csv ... [--run-id ...]` | IC/ICIR/quantiles → run 落盘（`run_id` + artifacts） |
| `qr backtest run --csv ...` | Alias of `pipeline research` |
| `qr analyze report --run ...` | Rebuild HTML/JSON report |
| `qr promote --run ... --model-id ... --version ... [--force]` | Create Model Package |
| `qr ops run --asof YYYYMMDD --csv ... [--package id==ver] [--mode paper\|signal] [--state ...] [--config ...]` | Order intents |
| `qr runs list\|show\|compare\|archive` | Experiment registry |
| `qr study decision --study ... --stage ... --summary ... --rationale ...` | Archive stage decision |
| `qr study list --study ...` | List decisions for a study |

`--csv` may be repeated for multi-file ingest where supported.

Global flags (work after subcommands): `--format json|text`, `--quiet`, `--run-id`.

## Ingest aliases (example)

CSV columns → domain fields via `ingest.aliases`:

| Domain | Example CSV |
|--------|-------------|
| `instrument` | `code` |
| `entry_intent_date` | `buy_date` |
| `exit_intent_date` | `sell_date` |
| `features.*` | e.g. `box_quality`, `%B` → `pct_b` |

Default `decision_date = entry_intent_date`.

## Signal YAML

```yaml
signals:
  filters:
    - { field: features.box_quality, op: ge, value: 0.94 }
  rank_by:
    - { field: features.bandwidth_percent, ascending: true }
```

## Walk-forward rules

- Fold key: `entry_intent_date`
- Purge: IS events whose holding overlaps OOS are removed from optimize objective
- OOS eval: only events with `entry_intent_date ∈ OOS`; PnL through actual exit
- Promotion gate: `n_oos_folds >= gates.min_oos_folds` (default 2)

### Train / test mapping (agent must follow)

- **Train+val**: years fed to `pipeline optimize` / WF (≥2 calendar years). Engine uses expanding/rolling IS→OOS folds with purge.
- **Test (holdout)**: reserve the last year (or explicit CSV); **never** include in optimize `--csv`. Evaluate frozen config once after tuning.
- Single-year optimize falls back to full-sample BT inside Optuna → treat as exploratory only, not promotable evidence.

## Domain glossary

| Concept | Key / field |
|---------|-------------|
| Initial cash | `portfolio.starting_cash` |
| Max name weight | `portfolio.max_weight` |
| Max new entries / day | `portfolio.max_new_entries_per_day` |
| GFD | `execution.order_validity_sessions=1` |
| GTD | `execution.order_validity_sessions>1` |
| Entry filter anchor | `execution.entry_filter.ref=decision_prior_close` |
| Planned entry/exit | `entry_intent_date` / `exit_intent_date` |
| PIT adjustment | `adjustment.as_of` |

## Exit priority (risk)

`stop_loss` → `take_profit` → `max_hold` → `exit_intent` → `deferred_exit`  
T+1: no same-session exit (`asof_session > entry_session`).

## Tests

```bash
pytest -q
```

Marker `e2e_local`: needs local zer0share + full event CSVs; do not require in CI-less agent loops unless user asks.

## Sample data

Under `workspace/events/` (if present): multi-year consolidation scan merges and small heads for smoke tests. Prefer small CSV for first agent iteration; scale up after ping + validate-events succeed.
