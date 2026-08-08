# qresearch CLI & layout reference

Agent research loop lives in [SKILL.md](SKILL.md); phases: [factor-analysis.md](factor-analysis.md) → [strategy-design.md](strategy-design.md) → [backtest-optimize.md](backtest-optimize.md); hard vetoes [quality-gates.md](quality-gates.md); index [research-loop.md](research-loop.md). This file is CLI/layout only. **CLI `best_value` ≠ skill 定稿。**

## Prices / 前复权

zer0share has **no** as-of qfq API (`pro_bar(adj=qfq)` is window-end). qresearch preloads raw `daily` + `adj_factor` once per study, then applies PIT qfq at read time: `price[t|T] = raw[t] * adj[t] / adj[T]` (`PricePanel.get(..., asof=T)`). Price-panel cache prefix is `pit_raw_v2_*`; its `.meta.json` sidecar retains the source `data_fingerprint`. Older `pit_raw_v1_*` entries lack the historical-limit schema and are not cache hits; use `qr data clear-cache` only when a manual cache reset is needed.

## Daily open execution / historical limits

For a daily open fill, qresearch uses zer0share's **same-session historical** `up_limit` and `down_limit`: an open touching limit-up rejects a buy, an open touching limit-down rejects a sell, and a suspended session does not fill. It does not model queue position, limit release timing, or the order book. Factor IC and theoretical forward-return calculations do **not** receive these fill filters; they are theoretical price measures, whereas backtests apply the execution constraint.

## Layout

```
configs/examples/       # 只读模板
configs/experiments/    # 实验 YAML 落盘（非知识库；勿通读当起点）
workspace/
  events/ | events_ascii/   # 事件 CSV（只读）
  runs/                     # 实验产物
  studies/                  # decision 日志
  models/                   # promote 包
  cache/                    # 行情缓存
```

Run layout:

```
workspace/runs/<run_id>/{meta.json,config.snapshot.yaml,artifacts/,report/}
workspace/studies/<study_id>/{INDEX.md,decisions/*.md|*.json}
workspace/models/<model_id>/<version>/{spec.yaml,provenance.json,metrics_oos.json,report/}
```

## zer0factor feature snapshots

`AppSettings` locates the existing dependency with `ZER0FACTOR_ROOT`, `ZER0FACTOR_FACTOR_DIR`, and `ZER0FACTOR_DB_PATH`. The provider opens `FactorStorage(..., init_db=False)` only after all three paths exist; it does not create a fallback directory, database, or factor data. Each declared `features.refs` entry is read once, shifted by its `availability_lag_sessions` over the trading calendar, and joined only where `available_session == asof_session` (no stale-value forward fill). The frozen snapshot manifest records per-factor coverage, source fingerprints, zer0factor revision/package fallback, and `feature_snapshot_hash`.

Research artifacts (typical): `sample_profile.json`（含 `years` / `years_span`）, `ic_summary.csv`, `icir_summary.csv`, `alpha_beta_summary.csv`, `quantile_returns.csv`, `factor_corr.csv`, `factor_diagnostics.json`, `preprocess_report.json`, `events_preprocessed.parquet`, `sweep_grid.csv`, `sensitivity_grid.csv`, `trades_diagnostics.json`.

## CLI map

| Command | Purpose |
|---------|---------|
| `qr version` | Package version |
| `qr data ping` | zer0share LocalPro import/path check |
| `qr data validate-events --csv ... [--config ...]` | Ingest mapping / schema check；summary 含 `board` / `n_limit10` / `n_limit20` |
| `qr data clear-cache` | Drop `workspace/cache` price panels |
| `qr pipeline research --csv ... [--config ...] [--run-id ...] [--n-trials-assumed N]` | Full research run (+ sample profile, IC/ICIR/quantiles, gates) |
| `qr pipeline optimize --csv ... [--feature ...] [--side auto\|high\|low] [--keep-frac ...] [--n-trials N]` | 单特征方向感知分位门槛网格 + WF |
| `qr pipeline sweep --csv ... --set ... [--metric sharpe] [--max-grid N]` | 多 filter 网格；`.value=` / `.between=lo:hi,...`；行含 `n_events_kept`；禁止与 sensitivity 联乘 |
| `qr pipeline sensitivity --csv ... [--cost-mult ...] [--stop ...] [--take ...] [--max-hold ...] [--max-weight ...] [--max-new ...] [--sizing-base cash\|nav] [--max-names-per-industry ...] [--max-new-per-industry ...] [--max-grid N]` | 执行/组合网格；行含 `mean_invested`（no promote） |
| `qr validate rolling --csv ... [--config ...]` | Walk-forward only |
| `qr factor ic --csv ... [--feature ...]` | Event IC for one feature |
| `qr factor preprocess --csv ...` | 强制跑预处理并落盘（配置默认可仍为 false） |
| `qr factor compare --csv ... [--run-id ...]` | IC 诊断落盘；信封含 `corr_top_pairs` / `monotonicity.shape` / `rejected_constant` |
| `qr factor band-ic --csv ... --feature ... --lo ... --hi ... [--inside-feature ...] [--horizons ...]` | 全样本 vs 带内 Rank IC（区间假说；仅 train） |
| `qr backtest run --csv ...` | 瘦别名；研究请用 `pipeline research`（含 `--n-trials-assumed`） |
| `qr analyze report --run ... [--train-run ...] [--validate-run ...] [--holdout-run ...] [--holdout-stress-run ...] [--full-run ...]` | 重建报告；多窗切分对照（全样本仅披露） |
| `qr analyze trades --run ...` | 只读成交/仓位/拒单/分年诊断 → `trades_diagnostics.json` |
| `qr config new --out configs/experiments/<name>.yaml --study-id ... [--from examples/...] [--set k=v]` | 开局脚手架：examples→experiments；强制空 signals；拒写 examples/ |
| `qr config apply-best --from-run ... --out configs/experiments/<name>_vN.yaml` | 迭代写回候选 YAML（拒 examples/）；**须再 research + quality-gates 验收** 才定稿 |
| `qr promote --run ... --model-id ... --version ... [--force]` | Create Model Package；门禁失败 exit 4 |
| `qr ops run --asof YYYYMMDD --csv ... [--package id==ver] [--mode paper\|signal] [--state ...] [--config ...]` | Order intents（单 package） |
| `qr runs list\|show\|compare\|archive` | Experiment registry |
| `qr study decision --study ... --stage ... --summary ... --rationale ...` | Archive stage decision（stage ∈ `_STAGES`；book freeze 用 `other`） |
| `qr study list --study ...` | List decisions for a study |

多策略组合：无专用 book 命令；分腿 `ops run` + 纸面合成见 [multi-strategy-portfolio.md](multi-strategy-portfolio.md)。

`--csv` may be repeated for multi-file ingest where supported.

Global flags (work after subcommands): `--format json|text`, `--quiet`, `--board limit10|limit20|all`。  
`--board` 覆盖 YAML `ingest.board`（默认 `limit10`）。`--run-id` 仅部分命令支持，不是全局旗标。

## Ingest aliases（列映射示意，非策略）

CSV columns → domain fields via `ingest.aliases`（以实际 CSV 表头为准）：

| Domain | Typical CSV column |
|--------|--------------------|
| `instrument` | `code` |
| `entry_intent_date` | `buy_date` |
| `exit_intent_date` | 常见映射 `sell_date`（若仅供参考 → 见 strategy-design / quality-gates G0，勿放入 `exit_priority`） |
| `features.*` | 数值/类别列 → `features.<name>`（别名表见 examples 骨架） |

Default `decision_date = entry_intent_date`。上表**不是**推荐 filter/rank。

### Board split（按涨跌停制度）

- `ingest.board`: `limit10`（默认，约 10% 涨跌停）| `limit20` | `all`
- `limit20` 识别：规范化后前缀 `688`/`689`（科创）+ `300`/`301`（创业）
- 两类微观结构差异大：**默认只跑 limit10**；20% 板用 `board: limit20` 或 `--board limit20` 单独研究，勿混样本下同一结论

## Signal YAML（示意结构；字段须来自因子证据，勿当默认策略）

```yaml
signals:
  filters:
    - { field: features.<from_ic>, op: ge, value: <thr> }
  rank_by:
    - { field: features.<from_ic>, ascending: <bool> }
```

## Walk-forward rules

- Fold key: `entry_intent_date`
- Purge: IS events whose holding overlaps OOS are removed from optimize objective
- OOS eval: only events with `entry_intent_date ∈ OOS`; PnL through actual exit
- Promotion gate: `n_oos_folds >= gates.min_oos_folds` (default 2)

### Train / test mapping (agent must follow)

- Write `evaluation:` before tuning (train / validate / holdout final / holdout stress / full disclose).
- **无固定 train:test 百分比**；厚度用相对量（见 quality-gates G9*）：`Y_train≥2` 才正式搜参；holdout final ≥1 自然年且 `N_holdout ≥ max(M, 2M)`；`Y_all≥4` 时默认要有 validate。
- **Train**: optimize / sweep / sensitivity / WF 只用这些年（可含截断年）。
- **Validate**: 冻结窗；**never** tune；缺省时须 `split_no_validate`。
- **Holdout final**（decision `stage=holdout`，summary 写 final）：终测；**never** tune。
- **Holdout stress**（同 stage，summary 写 stress）：压力年；差≠机械否决；只归因；不替代 final。
- **Full**：disclose only；须含全部可用事件年；不调参、不单独 promote.
- **时序**：train → validate → holdout final；`--csv` 必须对齐声明年（G9e）。
- **充分利用 events**：按 `sample_profile.years` / `years_span` 覆盖选年；勿因截断/年未过完丢弃。
- Single-year optimize → `split_exploratory` only.
- Optimize side: `auto` from `expected_sign` / `rank_by.ascending`.

### `evaluation` / gates（评估）

| Key | Meaning |
|-----|---------|
| `evaluation.primary_metric` | `absolute` \| `excess`（镜像到 `gates.primary_metric`） |
| `evaluation.train_years` / `validate_years` | 声明用；引擎不自动切 CSV |
| `evaluation.holdouts[].role` | `final` \| `stress` |
| `gates.min_ann_excess` / `min_information_ratio` | 可选；默认 `null`=关 |
| `mean_invested` | `1 - cash/nav`；research metrics / 报告主区 |
| 区间因子 | `filters` + `op: between`；形状 `u/inv_u/hump` → `band-ic` → sweep `.between=`；勿当唯一 `rank_by` |
| 窄带 | train 带内事件建议 ≥ max(50, 2×min_trades)；sweep 披露 `n_events_kept` |

## Domain glossary

| Concept | Key / field |
|---------|-------------|
| Initial cash | `portfolio.starting_cash` |
| Max name weight | `portfolio.max_weight` |
| Max new entries / day | `portfolio.max_new_entries_per_day` |
| Max names / industry (held) | `portfolio.max_names_per_industry` (null=off; pretrade) |
| Max new / industry / day | `portfolio.max_new_per_industry_per_day` (null=off; pretrade) |
| Industry field | `portfolio.industry_field` (default `features.industry`; event PIT) |
| Industry rejects | `industry_held_cap` / `industry_daily_cap`（缺行业默认仍可开仓） |
| GFD | `execution.order_validity_sessions=1` |
| GTD | `execution.order_validity_sessions>1` |
| Entry filter anchor | `execution.entry_filter.ref=decision_prior_close` |
| Planned entry/exit | `entry_intent_date` / `exit_intent_date` |
| Price adj mode | `adjustment.mode`（qfq=会话 PIT；`as_of` 仅标签/兼容，不参与 peek） |

## Exit priority (risk)

引擎默认顺序：`stop` → `take_profit` → `max_hold` → `exit_intent` → `deferred_exit`  
（阈值字段仍是 `risk.stop_loss` / `take_profit`。）  
若退出日仅供参考：YAML 中从 `exit_priority` **省略** `exit_intent`（G0）。  
定稿前用 `analyze trades` 检查退出份额是否经济可兑现（G2/G3）。  
T+1: no same-session exit (`asof_session > entry_session`).

引擎未提供的方法：勿在研究结论中假装已做。

## Sample data

事件在 `workspace/events/` / `events_ascii/`（只读）。先 `ping` + `validate-events`，再按 `sample_profile` 扩年；勿为图省事丢可用年。
