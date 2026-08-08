---
name: qresearch
description: 编排 qrsearch 的 PIT 市场因子物化、train-only zer0factor 评估与按角色冻结回测；用户提到 qr、因子分析或市场研究时使用。
---

# qresearch — market research workflow

qresearch 是 market-only、冻结数据集优先的研究内核。zer0share 提供 PIT universe 和行情；zer0factor 提供已物化因子与 train-only 评估。历史 `workspace/events/**` 仅为只读归档，产品没有 event/CSV/ops 命令。

## Required workflow

1. 阅读 [reference.md](reference.md) 与 [factor-analysis.md](factor-analysis.md)。
2. `qr data ping --format json --quiet`，随后 `qr research factors --format json --quiet`。
3. 用 `qr config new` 从 `configs/examples/market_factors.yaml` 新建实验；填写真实 universe、日期、因子 refs 与互不重叠的 temporal roles。
4. `qr research materialize --config <yaml> --run-id <id> --format json --quiet`。
5. `qr research evaluate --config <yaml> --run-id <id> --format json --quiet`；只根据该 run 的 train zer0factor 证据写 `factor_analysis` decision。
6. 仅在同一个已评估 run 上运行策略：
   `qr pipeline research --config <yaml> --run-id <id> --role <train|validate|holdout_final|holdout_stress> --format json --quiet`。
7. 只有 `train` 可用于 `pipeline optimize`、`pipeline sweep` 与 `pipeline sensitivity`；所有命令均必须提供 `--run-id` 和 `--role train`。

## Hard constraints

- Agent I/O 使用 `--format json --quiet`；stdout 为单一 JSON 信封。
- pipeline 从既有 `dataset.parquet` 读取一个显式 role，不会重新物化，也不会重新查询 zer0factor。
- 因子选择只看 train；validate 与 holdout 只能用于确认既定策略，不能反向选因子或调参。
- market membership 来自 daily zer0share universe snapshots；`st_filter_status` 必须为 `full` 才可 promote。`listed_only`、`unknown` 与 `mixed` 只可研究、不可推广。
- 需要显式 `risk.max_hold_sessions`，并在 `risk.exit_priority` 中保留 `max_hold`。市场路径没有事件型计划退出信号。
- 空 universe、零因子覆盖、无标签、缺失 screening artifacts、空 role 都是失败，绝不静默跳过。

## Gate boundary

当前自动执行：冻结 config/hash 一致性、已完成 factor screening、role 非空、search 的 train-only 限制，以及 promote 前 ST filter 为 full。样本厚度、统计显著性、经济阈值与多窗口稳定性仍是人工审阅清单，不能宣称已由 CLI 自动放行。

专题说明： [strategy-design.md](strategy-design.md)、[backtest-optimize.md](backtest-optimize.md)、[quality-gates.md](quality-gates.md)、[research-loop.md](research-loop.md)。
