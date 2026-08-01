# 回测与参数优化（Backtest & optimization）

入口：[SKILL.md](SKILL.md)。在信号定制之后执行；与 [strategy-design.md](strategy-design.md) 的执行/风控层衔接。

## 目标

评估经济表现与门禁；在**训练集**上做执行/风控敏感度或参数搜索；**holdout 只评估一次**。

## 样本切分（强制）

| 角色 | 做法 |
|------|------|
| 训练 + 验证 | 多年 CSV，**不含** holdout；WF / optimize / sensitivity 只用这些年 |
| 测试 holdout | 最后 1 个自然年（或用户指定）；永不进入 optimize/sensitivity 搜参 |
| 全样本终测 | 参数冻结后，对 **train+holdout 全部年份** 再跑一次 `pipeline research`（披露完整曲线与分年表）；**不能代替** holdout 门禁，也 **禁止** 看完全样本再调参 |

在 `hypothesis.statement` 与回复中写明：`train=..., holdout=...`。

## A. 回测 research

```bash
qr pipeline research --csv <train_years...> --config configs/experiments/<file>.yaml \
  --n-trials-assumed <N> --format json --quiet

qr analyze report --run <run_id> --format json --quiet
```

记下：`run_id`、报告路径、`summary.metrics`、`summary.gates`、`pit_status`、`trade_stats`。

### 门禁语义

| 字段 | 含义 |
|------|------|
| `structural_passed` / `passed` | 笔数、OOS folds 等 → 研究可继续 |
| `economic_passed` | Sharpe / 回撤等 |
| `promotable` | 结构 ∧ 经济（默认）→ 才可考虑 promote |

**勿**把结构过关当成可实盘。

### 分析清单

- [ ] `config.snapshot.yaml` 是否为本轮 YAML  
- [ ] sharpe / max_dd / total_return / n_trades  
- [ ] IR / 换手 / 容量（缺数据勿编造）  
- [ ] deflated_sharpe、`n_trials`  
- [ ] PIT：`warn` 常见；`fail` 先修数据  
- [ ] 退出原因占比（大量 `exit_intent` vs stop/tp）  
- [ ] 拒单原因（配额 / 涨跌停 / filter）

## B. 执行/风控敏感度（信号冻结后必做，才算完整策略）

因子 IC **不能**直接给出 stop/take/validity。信号冻结后：

```bash
qr pipeline sensitivity --csv <train_years...> --config <signal_frozen.yaml> \
  --cost-mult 1,1.5,2 \
  --stop -0.05,-0.086,-0.12 \
  --take 0.10,0.158,0.20 \
  --max-grid 27 \
  --format json --quiet
```

- 可按需加 validity / lag 的对比（改 YAML 多跑几个 research，或扩网格）
- `n_trials_assumed` 计入网格点数
- 选稳健格（如成本×2 后仍可接受）→ **新 YAML** → 再 `pipeline research`（训练年）
- **敏感度 run 本身不 promote**

未跑本节就交付「完整策略」→ 违规；须声明执行层仍为模板。

## C. 参数优化（Optuna：默认应做，非可偷懒跳过）

与 §B 分工：**sensitivity = 执行/风控网格（必做）**；**optimize = 信号侧连续/阈值参数搜索（默认应做）**。

默认研究闭环到交付完整策略时，训练年应至少跑一轮 `pipeline optimize`（或等价的手动网格），再冻结。  
**仅下列情形可跳过**，且须在 study decision 写明理由：

- 用户明确说本次不做 Optuna / 只做结构对比  
- 信号已无待搜旋钮（阈值、权重、持有期等均已由因子结论或 sensitivity 定死）  
- 上一轮 optimize 刚冻结且本轮只改结构、不重搜同一空间  

禁止把「可选」理解成「能省则省」。未做 optimize 时，结论须标注「信号参数未搜索，仅结构/模板阈值」。

仅在信号骨架已由因子选定、且通常在 §B 定稿执行层之后（或与之交错但一次只动一类旋钮）：

```bash
qr pipeline optimize --csv <train_years...> --config <exp.yaml> \
  --n-trials 20 --feature features.<best> --format json --quiet
```

规则：

1. 仅训练年；≥2 个自然年才算正式（引擎 WF）；单年 = 仅探索。  
2. `best_params` 写回**新** YAML 并冻结。  
3. 训练年 `pipeline research --n-trials-assumed N`。  
4. holdout **单独** research/validate **一次**；差则否决，禁止用 holdout 重搜。  
5. 一次只动一类旋钮（signals **或** risk **或** portfolio），便于归因。

## D. 对比与迭代

```bash
qr runs compare --runs <id1>,<id2> --format json --quiet
```

| 观察 | 动作 |
|------|------|
| 信号与 IC 方向矛盾 | 回 [factor-analysis.md](factor-analysis.md) / 改 signals |
| 结构过、经济不过 | 降换手或跑 sensitivity；禁止 promote |
| 成交过少 / 配额拒单 | 放宽 filter 或提高日限入 |
| 模板 stop/tp 主导盈亏 | **必须**跑 sensitivity，不得只改信号声称完成 |
| 连续 2–3 轮无改进 | 停止，对比 runs，给出推荐/否决 |

默认最多 **3** 轮「改策略/优化→回测」；更多需用户同意。

## E. 全样本终测（冻结后必做）

holdout 评估完成后（无论通过与否，只要还要交付最终报告）：

```bash
# 全部年份 CSV（训练 + holdout），配置已冻结
qr pipeline research --csv <all_years...> --config <frozen.yaml> \
  --n-trials-assumed <N> --format json --quiet
qr study decision --study <study_id> --stage full_sample \
  --summary "全样本 run=<id>; sharpe=..; 分年见报告" \
  --rationale "参数已冻结；本 run 仅披露完整区间，不参与调参" \
  --run <run_id> --config <frozen.yaml> --format json --quiet
```

向用户同时给出：训练年 / holdout / **全样本** 三个 `run_id`（若都跑过）。晋升仍以训练 WF + holdout 为准。

## F. 晋升（可选，用户明确要求）

```bash
qr validate rolling --csv <train_years...> --config <final.yaml> --format json --quiet
qr promote --run <run_id> --model-id <id> --version <ver> --format json --quiet
```

仅 `promotable=true` 且用户要求时；`--force` 需用户明确。  
**不要**用全样本 run 的好看指标去 promote 而跳过 holdout。

## 试次（deflated Sharpe）

- 第 1 轮 research：`n_trials_assumed=1`（或配置值）
- 每多一轮改 YAML 再 research：+1
- optimize `N` 试次 / sensitivity 网格：后续 research 用 ≥N

## Anti-patterns

- 未做因子就宣称因子驱动策略  
- 只改 signals、执行层抄模板却称「完整策略」  
- holdout 参与 optimize / 看完 holdout 再调参  
- 口头改参不写 YAML  
- 为冲指标改 costs  
- 单年全样本 optimize 当可晋升证据  

## 本阶段交付

- `run_id`（训练 / holdout / 全样本）、配置路径、报告路径  
- 关键指标 + 分年年化/超额（报告内表格）  
- 门禁：`structural` / `economic` / `promotable`  
- 执行层是否已经 sensitivity 定稿  
- 各阶段 `qr study decision` 已写入  
- 下一动作或停手理由  
