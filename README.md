# qresearch

Event-driven A-share research kernel and ops CLI for research agents.

## Install

```bash
cd qrsearch
python -m pip install -e ".[dev]"
```

## Environment

Copy `.env.example` to `.env` (optional):

| Variable | Meaning |
|----------|---------|
| `ZER0SHARE_ROOT` | Path to zer0share repo (for `LocalPro` import) |
| `ZER0SHARE_DATA` | Path to local parquet data directory |
| `QRESEARCH_EVENTS` | Default events path / directory |

Market data is loaded via zer0share `LocalPro`. This project does **not** depend on `vnpy` or `vnpy_portfoliostragtegy`.

## Quick start

```bash
# check vendor
qr data ping --format json

# validate event CSV mapping
qr data validate-events --csv workspace/events/平台期扫描_批量_2019_合并_0.94.csv --config configs/examples/event_factors.yaml

# research pipeline (needs zer0share daily data)
qr pipeline research --csv workspace/events/平台期扫描_批量_2019_合并_0.94.csv --config configs/examples/event_factors.yaml --format json --quiet
```

Agent-friendly I/O: always use `--format json --quiet`. stdout is a single JSON envelope (`schema_version`, `summary`, `artifacts`, `next_actions`, `error`). Logs go to stderr.

Global `--board limit10|limit20|all` overrides YAML `ingest.board` (default `limit10`: exclude 科创 688/689 + 创业 300/301). Study 20% boards separately with `--board limit20`.

Exit codes: `0` ok, `2` config, `3` data, `4` gate blocked, `5` dependency missing.

## Domain terms

| Concept | Config key |
|---------|------------|
| Initial cash | `portfolio.starting_cash` |
| Max name weight | `portfolio.max_weight` |
| Max new entries / day | `portfolio.max_new_entries_per_day` |
| Good-for-day order | `execution.order_validity_sessions=1` |
| GTD multi-session | `execution.order_validity_sessions>1` |
| Filter anchor | `execution.entry_filter.ref=decision_prior_close` |
| Planned entry/exit | `entry_intent_date` / `exit_intent_date` |
| PIT adjustment | `adjustment.as_of` |

## Daily open execution contract

Daily fills use `execution.price` (`open` by default; `close` is also supported). Eligibility always uses zer0share's **same-session opening** `up_limit` / `down_limit`: an open at the limit-up is not buyable, an open at the limit-down is not sellable, and suspended sessions do not fill. The model does not simulate order queues, limit-open release timing, or the order book. Factor IC and theoretical forward returns do **not** apply these fill filters; they measure the price relationship, while the backtest separately measures executable results.

## Layout

```
qresearch/              # library + CLI package
configs/                # strategy YAML (examples/ + experiments/)
tests/                  # unit tests (synthetic panel)
workspace/              # local inputs + artifacts (gitignored; see workspace/README.md)
  events/               # event CSVs
  runs/                 # experiment runs
  models/               # promoted model packages
  cache/                # price panel cache
```

## Tests

```bash
pytest -q
```

Synthetic fixtures cover GFD/GTD, T+1, sizing, pre-trade state sensitivity, and JSON envelope parsing. Local e2e against zer0share is optional.

## Agent workflow

This repo exposes **CLI tools only**. The research loop (factor analysis → write strategy YAML → backtest → quality gates → optimize / adjust → stop) is defined for agents in [`.agents/skills/qresearch/SKILL.md`](.agents/skills/qresearch/SKILL.md)（硬否决见同目录 `quality-gates.md`；开局脚手架 `qr config new`）。Codex reads the repository guidance in [AGENTS.md](AGENTS.md). Experiment configs go under `configs/experiments/`.

Engineering principles: **[AGENTS.md](AGENTS.md)** — any change must keep **config · tests · skill · md** aligned (see §1).
Product / research capability roadmap: **[ROADMAP.md](ROADMAP.md)**.

## Reports

`pipeline research` and `qr analyze report --run <run_id>` write a Chinese HTML report（净值/回撤/分年图可悬停读数，坐标含多档刻度；离线自包含，无 CDN）:

- `workspace/runs/<run_id>/report/research_report_zh.html` (alias of `conclusion.html`)
- `workspace/runs/<run_id>/report/conclusion.json` (metrics, IC, strategy, trade stats)
- `workspace/runs/<run_id>/artifacts/pit_audit.json` (PIT / adjustment disclosure)
- `workspace/runs/<run_id>/artifacts/metrics.json` (absolute + IR/turnover/capacity + deflated Sharpe)

Sections: 门禁、因子、策略、回测（相对基准/换手/容量）、交易统计、过拟合/试次、PIT 审计、拒单、Walk-forward、产物路径。

Research quality knobs in YAML `gates`: `n_trials_assumed`, `min_deflated_sharpe`, `max_n_trials`, `pit_strict`. CLI: `--n-trials-assumed`.

## CLI map

```
qr data ping | validate-events | clear-cache
qr config new --out configs/experiments/<name>.yaml --study-id ...   # scaffold from examples
qr config apply-best --from-run ... --out configs/experiments/<name>_vN.yaml
qr pipeline research|optimize|sweep|sensitivity
qr validate rolling
qr factor ic|compare|band-ic
qr backtest run
qr analyze report --run ...
qr promote --run ... --model-id ... --version ...
qr ops run --package ... --mode paper|signal --asof YYYYMMDD --csv ...
qr runs list|show|compare|archive
```
