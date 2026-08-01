# 策略定制（Strategy design）

入口：[SKILL.md](SKILL.md)。在 [factor-analysis.md](factor-analysis.md) 之后执行。

## 目标

把因子结论落成可回测的 YAML，并分清：**信号层** vs **执行/风控层**（后者不得只抄模板冒充因子结论）。

## 配置落盘

```
configs/examples/          # 模板：只读，先复制（新研究的唯一默认起点）
configs/experiments/       # 实验落盘区：每假设新文件；禁止开局 ls/通读当灵感来源
```

命名：`configs/experiments/<topic>_<yyyymmdd>_<tag>.yaml`  
每轮新假设 → **新文件**（v1/v2…），禁止原地覆盖唯一历史文件。  
续跑同一 study：从 `workspace/studies/<id>/` 决策链或用户给出的 config 路径接手，不要扫整个 experiments 目录。

```bash
# 概念步骤：复制后改
# configs/examples/event_factors.yaml → configs/experiments/<name>.yaml
```

## 两层定制（强制）

### A. 信号层（来自因子分析）

必须改（至少一处与因子证据相关），并**落实候选池**，禁止把多因子证据压成「随便两个」：

- `hypothesis.*`（含 `study_id`、`expected_sign` 覆盖所有入选特征）
- `signals.filters`（可多条）和/或 `signals.rank_by`（可多键）和/或 `signals.composite`（多成分）
- 因子结论若建议 ≥3 个可用特征：优先用「多 filter + 多 rank」或 `composite`，不要只抄模板单 filter + 单 rank

`ingest` / `costs` / `adjustment`：无新证据时保持与基底一致（勿为刷指标改佣金）。

### B. 执行 / 风控层（不得仅靠因子 IC）

模板里的下列项**默认不算「已定制」**：

- `risk.stop_loss` / `take_profit` / `max_hold_sessions`
- `execution.order_validity_sessions` / `lag_sessions` / `entry_filter`

正确来源：

1. 信号 YAML 先冻结（只含证据支持的 signals + 合理 portfolio）
2. 训练年跑 [backtest-optimize.md](backtest-optimize.md) 中的 **sensitivity / 持有期分析**
3. 把稳健格写回**新** YAML，再 research

若本轮未做 B 层证据：回复必须声明  
「退出/订单有效期仍为模板默认，非正式执行策略结论」。

## Portfolio（可与信号同轮，但要说明动机）

常见：降换手 → 降 `max_new_entries_per_day` / `max_weight`。  
这是组合约束，不是因子 IC 的直接推论——在 `hypothesis.statement` 里写一句即可。

## YAML 骨架示例

```yaml
hypothesis:
  id: ...
  statement: "train=2019-2024, holdout=2025; ..."
  expected_sign:
    features.pre_r1: negative

signals:
  filters:
    - { field: features.<qual_a>, op: ge, value: <thr> }
    - { field: features.<qual_b>, op: le, value: <thr> }   # 可多条
  rank_by:
    - { field: features.<primary>, ascending: <bool> }
    - { field: features.<secondary>, ascending: <bool> }   # 次排序
  # 或：
  # composite:
  #   enabled: true
  #   name: composite_score
  #   components:
  #     - { field: features.a, weight: 1.0, ascending: true }
  #     - { field: features.b, weight: 0.5, ascending: false }
  # rank_by: [{ field: features.composite_score, ascending: false }]

portfolio:
  max_weight: 0.08
  max_new_entries_per_day: 2

# 下列在未跑 sensitivity 前可暂留模板，但交付时必须标注「未定稿」
execution:
  order_validity_sessions: 5   # 待执行层证据
risk:
  stop_loss: -0.086            # 待执行层证据
  take_profit: 0.158
```

## 本阶段交付

- 实验 YAML 路径
- 假设与信号改动说明（1–3 句）
- 明确：执行/风控是「模板占位」还是「已由 sensitivity 定稿」
- **决策存档**（必做）：

```bash
qr study decision --study <study_id> --stage strategy_design \
  --summary "YAML=...; 入选因子=...; 结构=filters×N+rank×M|composite" \
  --rationale "映射候选池（非仅 Top2）；执行层=模板占位|已定稿" \
  --config configs/experiments/<file>.yaml \
  --next-action "backtest_train" --format json --quiet
```

- 下一动作：回测；若信号已稳 → 必做 sensitivity 再定稿 risk/execution
