---
name: qresearch
description: >-
  编排 qresearch（CLI: qr）做事件驱动研究闭环：因子分析→自定策略 YAML→回测→
  读中文报告→参数优化/改策略→再评估；以及 validate、promote、ops。项目只提供
  CLI，策略判断与迭代由本 skill 执行。在 qrsearch 仓库内研究、调参、对比 run，
  或用户提到 qr / 因子分析 / 定策略 / 回测优化时使用。不依赖 vnpy。
---

# qresearch

**分工**：仓库 = 确定性 CLI；**本 skill = 分析与执行剧本**（读 IC、改配置、迭代、向用户结论）。

包名 `qresearch`，入口 `qr`（或 `python -m qresearch`）。

## 硬性规则

1. Agent I/O：命令一律 `--format json --quiet`；只解析 stdout JSON 信封；日志在 stderr。
2. 勿编造行情；缺数据就停并提示同步 zer0share。
3. 勿引入 vnpy / `vnpy_portfoliostragtegy`；行情仅 `ZER0SHARE_ROOT` / `ZER0SHARE_DATA`。
4. 领域术语：`entry_intent_date` / `exit_intent_date` / `portfolio.max_weight` 等；CSV 的 `buy_date`/`code` 只是 ingest alias。
5. 无隐式 +1；延迟只用 `execution.lag_sessions`。
6. 大表落盘；对话里只引用路径与摘要。
7. **策略必须落盘为 YAML** 再回测；禁止只在口头改参数却仍跑旧 config。
8. 每轮迭代复制新配置（见下），保留历史 run 可对比；勿覆盖唯一的 `configs/examples/*` 而不留副本（改示例前先复制）。

退出码：`0` ok，`2` 配置，`3` 数据，`4` 门禁 blocked，`5` 依赖缺失。

## 默认研究闭环（用户未指定流程时执行这个）

目标：用因子证据定策略 → 回测验证 → 分析结果 → 优化或改策略 → 收敛或说明停手原因。

```
Task Progress:
- [ ] 0. 环境：ping + validate-events
- [ ] 1. 因子：factor compare（必要时再 ic）
- [ ] 2. 定策略：写入 configs/experiments/<name>.yaml
- [ ] 3. 回测：pipeline research
- [ ] 4. 分析：读信封 + research_report_zh / conclusion.json
- [ ] 5. 决策：改策略 / optimize / 扩样本 / 停止
- [ ] 6.（可选）多年 validate / promote / ops
```

细节与改参启发式见 [research-loop.md](research-loop.md)。CLI 全表见 [reference.md](reference.md)。

### 0) 环境与事件

```bash
qr data ping --format json --quiet
qr data validate-events --csv <events.csv> --config <base.yaml> --format json --quiet
```

`ping`/`validate` 失败则停止闭环。

### 1) 因子分析（先证据，后策略）

```bash
qr factor compare --csv <events.csv> --config <base.yaml> --format json --quiet
# 对候选特征加深：
qr factor ic --csv <events.csv> --config <base.yaml> --feature features.<name> --format json --quiet
```

从信封 `summary.rows`（或随后 research 的 `ic_summary.csv` / 报告「因子分析」）整理：

- 强特征：|Rank IC| 相对更高且符号稳定的 `features.*`
- 弱/噪声特征：|IC| 接近 0 或符号混乱 → 不要用作主排序，除非用户坚持

**禁止**：跳过因子步骤直接抄示例策略当「研究结论」（用户只要 smoke 测试除外）。

### 2) 定策略（agent 决策 → YAML）

1. 复制基底：`configs/examples/event_factors.yaml` → `configs/experiments/<experiment>_<yyyymmdd>_<tag>.yaml`
2. 按因子结论改至少一处与信号相关的项，并在回复中用 1–3 句写明**假设**（例：带宽越低越好 → `rank_by` 升序）。
3. 优先改：`signals.filters`、`signals.rank_by`；其次才动 `portfolio.*`、`risk.*`、`execution.*`。
4. `ingest` / `costs` / `adjustment` 无证据时保持与基底一致。

### 3) 回测

```bash
qr pipeline research --csv <events.csv> --config configs/experiments/<file>.yaml --format json --quiet
# 多轮迭代时带上试次（过拟合惩罚）：
qr pipeline research --csv <events.csv> --config configs/experiments/<file>.yaml --n-trials-assumed 3 --format json --quiet
```

记下 `run_id`、`artifacts.research_report_zh`、`summary.metrics`（含 IR / deflated_sharpe）、`pit_status`、`summary.gates`。

### 4) 分析结果

```bash
qr analyze report --run <run_id> --format json --quiet
```

必读：`summary.metrics`、`summary.trade_stats`（若有）、门禁原因；需要细节再打开：

- `workspace/runs/<run_id>/report/research_report_zh.html`
- `workspace/runs/<run_id>/report/conclusion.json`
- `workspace/runs/<run_id>/config.snapshot.yaml`（确认策略真是本轮 YAML）

### 5) 迭代决策（核心：skill 负责）

每轮结束必须明确下一动作（只选一类主路径）：

| 观察 | 动作 |
|------|------|
| 信号逻辑与 IC 方向矛盾 | 改 `signals`（新 YAML）→ 回步骤 3 |
| 收益/回撤差，但交易够、逻辑自洽 | 先改 `risk`/`portfolio` 或跑 optimize |
| 成交过少 / 拒单多为配额 | 放宽 filter 或提高 `max_new_entries_per_day` |
| 单年 `oos_folds<2` | 研究可继续；**不要**为冲门禁擅自 `--force` promote；扩多年 CSV 再 `validate rolling` |
| 连续 2–3 轮无改进 | **停止**，对比 runs 并给出推荐配置与否决理由 |

优化（在策略骨架稳定后）：

```bash
qr pipeline optimize --csv <events.csv> --config <exp.yaml> --n-trials 20 --feature features.<best> --format json --quiet
```

把 `best_params` **写回新 YAML**，再 `pipeline research` 验证；优化结果不经回测不得当最终策略。

对比：

```bash
qr runs compare --runs <id1>,<id2> --format json --quiet
```

默认最多 **3** 轮「改策略/优化 → 回测」；更多轮需用户同意或用户明确要求深挖。

### 6) 晋升与 Ops（闭环外可选）

```bash
qr promote --run <run_id> --model-id <id> --version <ver> --format json --quiet
qr ops run --package <id>==<ver> --mode paper --asof YYYYMMDD --csv <events.csv> --format json --quiet
```

仅用户明确要求时用 `--force`。`mode=signal` 且无持仓 state 不可当可实盘依据。

## 快捷路径（可跳过完整闭环）

| 用户意图 | 路径 |
|----------|------|
| 只检查环境/CSV | 步骤 0 |
| 只要示例配置跑通 | 0 → 用 `configs/examples/...` 做 3→4，并声明「未做因子定策略」 |
| 只要报告 | `analyze report --run ...` |
| 只要 IC | 步骤 1 |
| 实盘意图导出 | 已有 package 后步骤 6 |

## 配置与产物（速查）

| 区域 | 键 |
|------|-----|
| signals | `filters`、`rank_by` |
| portfolio | `starting_cash`、`max_weight`、`max_new_entries_per_day` |
| execution | `price`、`order_validity_sessions`（1=GFD）、`entry_filter` |
| risk | `stop_loss`、`take_profit`、`max_hold_sessions` |
| gates | `min_oos_folds`、`min_trades` |

中文报告：`workspace/runs/<run_id>/report/research_report_zh.html`（策略详情 = 本轮 YAML 快照，不是引擎另算的策略）。

## 回复用户时

- 写明：因子结论 → 策略假设 → `run_id` → 关键指标 → 下一动作或停手理由。
- 给出配置路径与报告路径；需要时用 `runs compare` 对比。
- 勿把父项目旧术语写入配置。

## 更多

- 闭环决策细则：[research-loop.md](research-loop.md)
- CLI / 目录：[reference.md](reference.md)
