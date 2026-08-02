# 质量闸门（Quality gates）

入口：[SKILL.md](SKILL.md)。本文件是 **Agent 强制否决表**：CLI 信封里的 `best_value` / `promotable` **不等于**可定稿。  
阈值一律用**相对量**（相对 `gates.min_trades`、事件池、退出份额）；**禁止**在此文件或其它专章写死可抄的止损/止盈/特征阈值。

及格线（夏普、年化等）只来自当次 `evaluation` + 用户声明，不内置进 skill。

## 符号（可执行定义）

| 符号 | 定义 |
|------|------|
| `M` | `gates.min_trades`（YAML；缺省按配置模型默认） |
| `Y_train` | `evaluation.train_years` 年数（至少按自然年计数） |
| `N_events` | 本轮 train ingest 后事件数（`sample_profile.n_events`） |
| `N_kept` | 硬过滤后保留事件数（`ranked_events` 行数，或 sweep 行的 `n_events_kept`） |
| `keep_frac` | `N_kept / N_events` |
| `N_sells` | `analyze trades` 卖出笔数 |
| `share(r)` | 退出原因 `r` 占总卖出份额（`exit_reasons[].share`） |
| `pnl_sum(r)` | 退出原因 `r` 的盈亏合计（若信封有分组） |
| `I` | `mean_invested`（`1 - cash/nav`） |
| `K_density` | 密度系数，默认 **3** |
| `N_kept_min` | `max(M, K_density * M * max(Y_train, 1))` |
| `S_tp_ok` | 若配置了非空 `take_profit`：通过条件为 **OR**——`share(take_profit) ≥ 0.15`，或（存在 `max_hold` 退出时）`share(take_profit) ≥ share(max_hold) / 3`；未配置 TP 则本条不适用 |
| `S_stop_bad` | 已配置非空 `take_profit` 时：`share(stop) > 0.45` **且** `share(take_profit) < 0.10` |
| `I_floor` | 当用户目标含年化或绝对收益时：要求 `I ≥ 0.25`；否则不强制（仍须披露） |

## 定稿定义

配置可称「完整策略 / 执行已定稿 / champion」**当且仅当**：

1. 信号层有因子证据（decision `factor_analysis` + `strategy_design`）；  
2. 执行/组合层经过 sensitivity（或用户书面跳过并声明模板）；  
3. 拟采用格与冻结 YAML 均通过下文闸门；  
4. 对应 `study decision` 已落盘（含否决过的更高夏普格，若有）。

**信封 `best_params.best_value` 只是候选，不是定稿。**

## 闸门表

| ID | 何时检查 | 触发条件 | 动作 |
|----|----------|----------|------|
| G0 | 开局 / `strategy_design` | 用户或数据声明退出日「仅供参考 / 扫描标签」 | `risk.exit_priority` **不得含** `exit_intent`；decision 写明 |
| G0b | 首轮 train `analyze trades` 后 | 未澄清且 `share(exit_intent)` 过高（经验：`> 0.30` 或为最大退出份额） | **停手**：澄清语义或去掉 `exit_intent`；禁止继续 optimize/sensitivity |
| G1 | 信号 YAML / sweep 后 | `N_kept < N_kept_min` 或 `keep_frac` 极低且硬过滤依赖近稀有二元字段 | **不得**在含年化/绝对收益目标的 study 标唯一 champion；可标 `signal_sparse` 旁路（decision 写 keep 比例与经济含义）；优先改 `rank_by`/composite 而非硬 `eq` |
| G2 | 候选 YAML 的 train research + `analyze trades` 后 | 已配置非空 `take_profit` 且不满足 `S_tp_ok` | **否决该格**（纸面盈亏比）；重搜 take/hold 或取消无效 TP |
| G3 | 同上 | 触发 `S_stop_bad` | **否决该格**（止损收割机）；重搜 |
| G4 | 同上 | 主盈利来自某退出类型（`pnl_sum` 最大） | decision **必须点名**；若主盈利是 `max_hold` 却声称「止盈止损定稿」→ 违规，改正表述或重搜 |
| G5 | 标 champion / 向用户报「达标策略」前 | 用户目标含年化或绝对收益，且 `I < I_floor` | **不得**标 champion；可报「高夏普稀疏信号旁路」并给 invested |
| G6 | 选格时 | 用户同时要求夏普与年化（或 evaluation 主指标+用户附加年化） | 选格表须并列夏普、年化（或 total_return）、`I`；**禁止**只按夏普 `best_value` 定稿 |
| G7 | sensitivity 定稿 | 网格未含成本乘数维，或入选格在 cost 加压下相对基准成本塌缩不可交代 | **不得**定稿；补跑含 cost 维的敏感性 |
| G8 | 板块 | 在 `all` 上得出的信号直接用于单一涨跌停板，或混板结论 | 否决；换板 = 新实验文件 / 新 study |

失败时：一次只回退**一类**旋钮（信号 **或** 执行/组合），写 decision，禁止联乘重扫。

## 旁路标签（允许披露，不可冒充完整策略）

| 标签 | 含义 | 对用户必须说明 |
|------|------|----------------|
| `signal_sparse` | **触发 G1**（未达密度下限），边沿或可但不可支撑年化目标 | keep 比例、为何不扩池 |
| `execution_template` | 未跑 sensitivity 或未过 G2–G4/G7 | 「执行层仍为模板」 |
| `metric_sidecar` | 单指标好看但未过 G5/G6 | 并列未达标目标 |

## `apply-best` 强制流程

网格信封通常**不含**完整退出结构；因此允许「先落盘候选、再闸门验收」，但**验收前不得称定稿/champion**：

```
候选格（optimize|sweep|sensitivity 的 best 或人工点名的次优格）
  → apply-best（或手写）→ configs/experiments/<name>_vN.yaml   # 仅候选
  → train research + analyze trades
  → 过 G1–G7（适用项；sensitivity 定稿必过 G7）
  → 通过：study decision 宣布冻结；可进 OOS
  → 失败：该 vN 不得当 champion；换下一格或回退一层；decision 记录「否决的更高夏普格」与失败闸门 ID
```

禁止：`best_value` 最高 → apply-best → **跳过 research/闸门** → 称完整策略。

## 与 `analyze trades` 字段对照

| 闸门 | 主要读 |
|------|--------|
| G0b, G2–G4 | `exit_reasons` / `exit_reason_groups`（share、pnl） |
| G5, G6 | `mean_invested`、`empty_cash_share`、metrics.ann_return / sharpe |
| G1 | `ranked_events` 规模、sweep `n_events_kept`、`sample_profile` |

```bash
qr analyze trades --run <research_run_id> --format json --quiet
```

## 过闸时写入 decision

触发/通过的闸门 ID；否决则回退阶段 + 下一动作；旁路则标签 + 对用户一句限制说明。  
定稿/停手汇报口径见 [research-loop.md](research-loop.md)。
