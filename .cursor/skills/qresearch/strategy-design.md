# 策略定制（Strategy design）

入口：[SKILL.md](SKILL.md)。在 [factor-analysis.md](factor-analysis.md) 之后执行。  
密度与定稿否决见 [quality-gates.md](quality-gates.md)。

## 目标

把因子结论落成可回测的 YAML，并分清：**信号层** vs **执行/风控层**（后者不得只抄模板冒充因子结论）。

## 配置落盘

```
configs/examples/          # 模板：只读（唯一默认起点；勿手改）
configs/experiments/       # 实验落盘区：每假设新文件；禁止开局 ls/通读当灵感来源
```

命名：`configs/experiments/<topic>_<yyyymmdd>_<tag>.yaml`  
每轮新假设 → **新文件**（v1/v2…）。续跑同一 study：从 `workspace/studies/<id>/` 或用户给出的路径接手。

```bash
qr config new \
  --out configs/experiments/<topic>_<yyyymmdd>_<tag>.yaml \
  --study-id <study_id> \
  [--set hypothesis.id=...] \
  --format json --quiet
```

读信封 `summary.out`；signals 已清空；再按因子证据写 filters/rank_by；`evaluation` 年份从 `sample_profile` 填。  
迭代写回：`apply-best`（候选）→ research → [quality-gates.md](quality-gates.md) 才冻结。落盘反模式见 [research-loop.md](research-loop.md)。

## Ingest / 退出日语义（强制）

| 情况 | 动作 |
|------|------|
| 用户或数据说明 `exit_intent_date` / CSV 退出列「仅供参考、扫描标签、非计划卖出」 | `risk.exit_priority` **去掉** `exit_intent`；decision 写明（闸门 G0） |
| 未澄清 | 首轮 train 后看 `analyze trades`：若 `exit_intent` 主导退出 → 停手澄清（G0b） |
| 退出列确为策略计划退出 | 可保留默认 priority；仍须在 statement 写明 |

`entry_intent_date` 仍为事件决策/意图入场日；无隐式 +1，延迟只用 `execution.lag_sessions`。

## 两层定制（强制）

### A. 信号层（来自因子分析）

- `hypothesis.*`（`study_id`、`expected_sign` 覆盖入选特征）
- `signals.filters` / `rank_by` / `composite` 落实候选池（非仅 Top2）
- 区间因子：`op: between` 进 filters；单调因子：`rank_by` 或单侧 filter
- 特征用**原始**列名；`__prep` 仅诊断
- 写 `evaluation:`（与 [backtest-optimize.md](backtest-optimize.md) 一致）

**密度（链到 G1）**：硬过滤前估计 `keep_frac` / 稀有二元字段频次。过稀 → 优先 rank/composite 加权，或声明 `signal_sparse` 旁路；有年化目标时不得单独当 champion。

`ingest.board`：与因子阶段一致；换板 = 新实验文件。勿为刷指标改 `costs`。

### B. 执行 / 风控层

下列默认**不算已定制**：`risk.stop_loss` / `take_profit` / `max_hold_sessions` / `order_validity_sessions` / `lag_sessions` / `entry_filter`。

正确来源：信号冻结 → train sensitivity → `apply-best`（候选）→ research → **过质量闸门** → 冻结。  
未做：声明 `execution_template`。

## Portfolio

动机写入 `hypothesis.statement`。行业 cap ≠ 因子行业中性。

```yaml
portfolio:
  industry_field: features.industry
  max_names_per_industry: <N_or_null>
  max_new_per_industry_per_day: <K_or_null>
```

`null`=关。启用时须说明 N/K 动机；**勿从本文抄具体整数当默认**。

## YAML 骨架（语法占位，非策略）

```yaml
hypothesis:
  study_id: <topic_tag>
  id: <id>
  statement: "train=<from sample_profile>; holdout=<...>; exit_semantics=<...>; ..."
  expected_sign:
    features.<from_evidence>: <positive|negative|band>

signals:
  filters:
    - { field: features.<from_evidence>, op: <ge|le|between|eq|...>, value: <from_train_evidence>, value_max: <if_between> }
  rank_by:
    - { field: features.<from_evidence>, ascending: <bool> }
  # composite: 可选；name 与 components 来自证据

portfolio:
  max_weight: <from_scaffold_or_motive>
  max_new_entries_per_day: <...>

execution:
  order_validity_sessions: <template_until_sensitivity>
risk:
  stop_loss: <template_until_sensitivity>
  take_profit: <template_until_sensitivity>
  max_hold_sessions: <template_or_null>
  exit_priority: <omit_exit_intent_if_reference_only>
```

## 本阶段交付

- 实验 YAML 路径  
- 信号结构说明；密度预期 / 是否 sparse 旁路  
- exit 语义与 `exit_priority`  
- 执行层：模板占位 vs 待 sensitivity  
- `study decision`（stage=`strategy_design`）  
- 下一动作：train research → `analyze trades` → 质量闸门 →（optimize/sweep）→ sensitivity  
