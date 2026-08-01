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

## 文档地图（按大类）

| 阶段 | 文档 | 负责什么 |
|------|------|----------|
| 1. 因子分析 | [factor-analysis.md](factor-analysis.md) | 候选池+冗余；多因子 filter/rank/composite（非仅 Top2） |
| 2. 策略定制 | [strategy-design.md](strategy-design.md) | YAML 落盘；落实候选池；信号层 vs 执行/风控层 |
| 3. 回测与优化 | [backtest-optimize.md](backtest-optimize.md) | research、sensitivity、optimize、holdout、门禁 |
| 编排索引 | [research-loop.md](research-loop.md) | 分支、停手、反模式速查 |
| CLI 全表 | [reference.md](reference.md) | 命令与路径 |

用户只点某一阶段时，**只读对应文档**，不必整本闭环。

## 硬性规则

1. Agent I/O：命令一律 `--format json --quiet`；只解析 stdout JSON 信封；日志在 stderr。
2. 勿编造行情；缺数据就停并提示同步 zer0share。
2b. **事件 CSV 只读**：禁止改/删/覆盖 `workspace/events/**`、`workspace/events_ascii/**`（含 Shell 重定向）。ingest 只用 `--csv`；预处理/衍生只写 `workspace/runs/<run_id>/artifacts/`。需要新样本时请用户提供，勿就地改原始文件。
3. 勿引入 vnpy / `vnpy_portfoliostragtegy`；行情仅 `ZER0SHARE_ROOT` / `ZER0SHARE_DATA`。
4. 领域术语：`entry_intent_date` / `exit_intent_date` / `portfolio.max_weight` 等；CSV 的 `buy_date`/`code` 只是 ingest alias。
5. 无隐式 +1；延迟只用 `execution.lag_sessions`。
6. 大表落盘；对话里只引用路径与摘要。
7. **策略必须落盘为 YAML** 再回测；禁止只在口头改参数却仍跑旧 config。
8. 每轮迭代复制新配置；勿覆盖唯一的 `configs/examples/*` 而不留副本。
8b. **勿通读 `configs/experiments/` 历史文件当起点**。该目录是落盘区（已 gitignore / cursorignore），不是知识库。仅当用户 `@` 指定、study decision / RUNS 指向某路径、或本会话刚写入时才读取；新假设从 `configs/examples/` 复制后写**新文件名**。
9. **参数优化必须分样本**：调参只用训练/验证；**禁止**在 holdout 上调参。
10. **完整策略 = 信号证据 + 执行/风控证据**。因子 IC 只支撑 signals；`risk.*` / `order_validity_sessions` 须经 sensitivity（或等价网格）定稿。未做则必须声明「执行层仍为模板默认」。
11. **决策必须落盘并与产物关联**：`hypothesis.study_id` 写入实验 YAML；每阶段 `qr study decision --study <id>`；有 run 时**必须**加 `--run <run_id>`（镜像进该 run，并写入 `meta.study_id`）。报告会拉取整个 study 决策链。
12. **调参分样本，终测用全样本**：optimize / sensitivity / 迭代只在训练年；holdout 只评估一次；**冻结参数后必须再对 train+holdout 全区间跑一次 research**（披露用，禁止据此再调参）。

退出码：`0` ok，`2` 配置，`3` 数据，`4` 门禁 blocked，`5` 依赖缺失。

## 决策存档（每阶段必做）

选定同一 `study_id`（如 `plat_box_2019_2025`），并写入 YAML：

```yaml
hypothesis:
  study_id: plat_box_2019_2025
```

阶段结束后：

```bash
qr study decision --study <study_id> --stage <stage> \
  --summary "<一句话结果>" --rationale "<决策逻辑>" \
  [--evidence path_or_json] --run <run_id> [--config <yaml>] \
  [--next-action "..."] --format json --quiet
# 然后刷新报告以带上决策链：
qr analyze report --run <run_id> --format json --quiet
```

| 位置 | 作用 |
|------|------|
| `workspace/studies/<id>/decisions/` | 全流程 canonical 决策链 |
| `workspace/studies/<id>/RUNS.md` | 关联过的 run / 报告路径 |
| `workspace/runs/<run_id>/decisions/` | 该次产物上的镜像 |
| `meta.json` 的 `study_id` | 报告加载决策链的钥匙 |
| 报告章节「0. 决策存档」 | 最终 HTML/JSON 内可见 |

| stage | 何时写 |
|-------|--------|
| `factor_analysis` | 因子结论与主因子选定后 |
| `strategy_design` | 实验 YAML 落盘后 |
| `backtest_train` | 训练年 research 后 |
| `sensitivity` / `optimize` | 网格或 Optuna 定稿后 |
| `holdout` | holdout 一次评估后 |
| `full_sample` | 全样本终测 research 后 |

## 默认研究闭环（用户未指定流程时）

```
Task Progress:
- [ ] 0. 环境：ping + validate-events
- [ ] 1. 因子分析 → factor-analysis.md → study decision
- [ ] 2. 策略定制（信号层）→ strategy-design.md → study decision
- [ ] 3. 回测 research（训练年）→ backtest-optimize.md §A → study decision
- [ ] 4. 信号冻结后：sensitivity 定稿执行/风控 → §B；写回新 YAML
- [ ] 5. 再 research；默认 optimize 一轮（仅训练年；跳过须写理由）→ §C
- [ ] 6. holdout 一次评估 → study decision
- [ ] 7. 冻结参数 → 全样本（train+holdout）research 终测 → study decision (full_sample)
```

```bash
# 0) 环境
qr data ping --format json --quiet
qr data validate-events --csv <events.csv> --config <base.yaml> --format json --quiet
```

细节、分支表、停手条件见各专章与 [research-loop.md](research-loop.md)。

## 快捷路径

| 用户意图 | 路径 |
|----------|------|
| 只检查环境/CSV | 步骤 0 |
| 只要因子分析 | [factor-analysis.md](factor-analysis.md) |
| 只要定策略 YAML | 因子结论已有 → [strategy-design.md](strategy-design.md) |
| 只要回测/优化 | 已有 YAML → [backtest-optimize.md](backtest-optimize.md) |
| 只要示例跑通 | 用 `configs/examples/...` research，并声明「未做因子定策略」 |
| 只要报告 | `qr analyze report --run ...` |
| 实盘导出 | 已有 package → `promote` / `ops`（见 reference） |

## 配置与产物（速查）

| 区域 | 键 |
|------|-----|
| signals | `filters`、`rank_by`、`composite` |
| portfolio | `starting_cash`、`max_weight`、`max_new_entries_per_day` |
| execution | `price`、`order_validity_sessions`、`entry_filter`、`lag_sessions` |
| risk | `stop_loss`、`take_profit`、`max_hold_sessions` |
| gates | `min_oos_folds`、`min_trades`、经济阈值 |

中文报告：`workspace/runs/<run_id>/report/research_report_zh.html`。

## 回复用户时

- 写明阶段：因子结论 → 策略假设（信号 / 执行是否定稿）→ `run_id` → 关键指标 → 下一动作。
- 给出配置路径与报告路径。
- 勿把父项目旧术语写入配置。
