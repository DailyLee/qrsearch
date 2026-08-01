# Research loop playbook

Agent owns judgment; CLI only computes. Use with [SKILL.md](SKILL.md).

## Roles

| Layer | Responsibility |
|-------|----------------|
| CLI (`qr`) | validate, IC, backtest, optimize, report files, promote, ops |
| Skill (you) | interpret IC, write strategy YAML, choose next step, stop rules |
| User | goal, data range, whether to promote / force gates |

## Experiment config layout

```
configs/
  examples/           # read-only templates (copy, don't silently overwrite)
  experiments/        # agent-written configs for each hypothesis
```

Naming: `configs/experiments/<topic>_<yyyy>_<tag>.yaml`  
Example: `configs/experiments/box_bw_rank_2025_v1.yaml`

Each new hypothesis → **new file** (v1, v2…). Snapshot in `workspace/runs/<run_id>/config.snapshot.yaml` is source of truth for what ran.

## Step 1 — Read IC

From `factor compare` / `ic_summary.csv` / report section「因子分析」:

1. Group by `feature`, look at Rank IC across horizons (1/5/10/20).
2. Pick **primary rank feature**: highest stable `|IC|` with consistent sign.
3. Pick **filter feature** (optional): quality-like field with positive IC or domain prior (e.g. `box_quality` high).
4. If top `|IC|` all &lt; ~0.02 and unstable → say so; still may backtest a simple baseline, but set expectations low.

Direction → `rank_by.ascending`:

- IC &gt; 0：特征越大越好 → `ascending: false`
- IC &lt; 0：特征越小越好 → `ascending: true`

## Step 2 — Write strategy YAML

Minimal change set from template:

```yaml
signals:
  filters:
    - { field: features.<filter>, op: ge, value: <threshold> }
  rank_by:
    - { field: features.<primary>, ascending: <bool> }
```

Then only if needed:

- Too many names / heavy rejects `max_new_entries_or_max_names` → raise `portfolio.max_new_entries_per_day` or tighten filter.
- Large drawdown → tighten `risk.stop_loss` or lower `portfolio.max_weight`.
- Few trades → loosen filter threshold or add horizons via more events (not fake data).

Do **not** retune `costs` / commission to chase metrics.

## Step 3–4 — Backtest & analyze checklist

After `pipeline research` / `analyze report`:

- [ ] `config.snapshot.yaml` matches intended experiment file
- [ ] `n_trades` vs `gates.min_trades`
- [ ] `sharpe`, `max_dd`, `total_return`
- [ ] **相对绩效**：`information_ratio`、`excess_return`、`tracking_error`（基准缺失则 `benchmark_available=false`）
- [ ] **换手 / 容量**：`ann_turnover`、`median_participation`（`capacity=unavailable` 时勿编造）
- [ ] **过拟合**：`deflated_sharpe`、`dsr_prob`、`n_trials`；多轮迭代必须递增试次
- [ ] **PIT**：读 `artifacts/pit_audit.json`；`status=warn` 常见（窗口终点前复权）；`fail` 须先修事件/数据
- [ ] `trade_stats.win_rate`, `profit_factor`, exit reason mix
- [ ] Reject top reasons (quota vs limit vs filter)
- [ ] `promotable` / `gate_reasons`（单年 `oos_folds<2` 预期失败）

### Trial count（deflated Sharpe）

- 第 1 轮 research：默认 `n_trials_assumed=1`（或 config `gates.n_trials_assumed`）
- 每多一轮「改 YAML 再 research」：+1
- 跑过 `pipeline optimize --n-trials N`：后续 research 用 `--n-trials-assumed N`（或更大）
- 可选门禁：`gates.min_deflated_sharpe`（如 `0.0`）；未配置则只披露不挡 promote

## Step 5 — Branching

```
IC done?
  no → factor compare
  yes → YAML hypothesis exists?
          no → write experiments/*.yaml
          yes → research
                 → improved vs best prior run?
                      yes → keep as champion; optional optimize on primary feature
                      no  → change ONE lever (signals OR risk OR portfolio); new YAML; research
                 → 3 rounds no improve → STOP + compare
```

### When to call `pipeline optimize`

- Skeleton (`rank_by` / filter field choice) already chosen from IC.
- Use `--feature` = primary feature.
- Always write `best_params` into a **new** YAML and re-run `pipeline research`.

### When to expand years

- User wants promote / OOS folds ≥ 2.
- Pass multiple `--csv` year files if supported, or a merged multi-year CSV.
- Run `validate rolling` before `promote`.

## Stop conditions

Stop the loop when any is true:

1. User only asked for smoke / single backtest.
2. Champion beats baseline on sharpe (or user metric) with acceptable DD, and further tweaks &lt; material gain.
3. 3 research rounds without improvement.
4. Data/dependency errors (`exit 3/5`).
5. User says stop.

On stop, report:

- Champion `run_id` + config path
- Factor rationale (1–3 bullets)
- Metrics + trade_stats highlight
- Why not promote (if blocked)
- Concrete next command if user wants to continue

## Anti-patterns

- Running research on `event_factors.yaml` and claiming “因子驱动策略” without IC step
- Editing parameters only in chat without writing YAML
- Overwriting previous experiment YAML in place
- Using `--force` promote to “finish the loop”
- Optimizing before choosing rank feature from IC
- Changing many levers in one round (cannot attribute effect)
