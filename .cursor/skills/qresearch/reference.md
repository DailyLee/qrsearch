# qresearch CLI & layout reference

Agent research loop (IC → strategy YAML → backtest → iterate) lives in [SKILL.md](SKILL.md) and [research-loop.md](research-loop.md). This file is CLI/layout only.

## Layout

```
qresearch/              # package + CLI
configs/examples/       # templates (copy before editing)
configs/experiments/    # agent-written hypothesis configs
tests/fixtures/         # synthetic panels for unit tests
workspace/              # local inputs + artifacts (gitignored)
  events/               # event CSVs
  runs/                 # immutable experiment runs
  models/               # promoted model packages
  cache/                # price panel cache
```

Run layout:

```
workspace/runs/<run_id>/{meta.json,config.snapshot.yaml,artifacts/,report/}
workspace/models/<model_id>/<version>/{spec.yaml,provenance.json,metrics_oos.json,report/}
```

## CLI map

| Command | Purpose |
|---------|---------|
| `qr version` | Package version |
| `qr data ping` | zer0share LocalPro import/path check |
| `qr data validate-events --csv ... [--config ...]` | Ingest mapping / schema check |
| `qr data clear-cache` | Drop `workspace/cache` price panels |
| `qr pipeline research --csv ... [--config ...] [--run-id ...] [--n-trials-assumed N]` | Full research run (+ deflated Sharpe trials) |
| `qr pipeline optimize --csv ... [--config ...] [--n-trials N] [--feature ...]` | Optuna + WF |
| `qr validate rolling --csv ... [--config ...]` | Walk-forward only |
| `qr factor ic --csv ... [--feature ...]` | Event IC for one feature |
| `qr factor compare --csv ...` | IC across `features.*` columns |
| `qr backtest run --csv ...` | Alias of `pipeline research` |
| `qr analyze report --run ...` | Rebuild HTML/JSON report |
| `qr promote --run ... --model-id ... --version ... [--force]` | Create Model Package |
| `qr ops run --asof YYYYMMDD --csv ... [--package id==ver] [--mode paper\|signal] [--state ...] [--config ...]` | Order intents |
| `qr runs list\|show\|compare\|archive` | Experiment registry |

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
