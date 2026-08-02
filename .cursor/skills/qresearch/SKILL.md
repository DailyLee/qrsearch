---
name: qresearch
description: >-
  编排 qresearch（CLI: qr）做事件驱动研究闭环：因子分析→自定策略 YAML→回测→
  质量闸门→参数优化/改策略→再评估；以及 validate、promote、ops。项目只提供
  CLI，策略判断与闸门由本 skill 执行。在 qrsearch 仓库内研究、调参、对比 run，
  或用户提到 qr / 因子分析 / 定策略 / 回测优化时使用。不依赖 vnpy。
---

# qresearch

**分工**：仓库 = 确定性 CLI；**本 skill = 分析与执行剧本**（读证据、过质量闸门、改配置、迭代、向用户结论）。

包名 `qresearch`，入口 `qr`（或 `python -m qresearch`）。

## 文风（全 skill 包）

- **允许**：CLI 旗标、配置键、占位符 `<train_csvs>`、`features.<from_evidence>`、相对阈值符号（见 [quality-gates.md](quality-gates.md)）。
- **禁止**：可粘贴的特征组合、止损止盈具体小数、keep-frac 数字串、行业 N/K「推荐值」、写死研究起止年。
- **信封 `best_value` / 引擎 `promotable` ≠ 可定稿**；定稿须过质量闸门。

## 文档地图

| 阶段 | 文档 | 负责什么 |
|------|------|----------|
| 1. 因子分析 | [factor-analysis.md](factor-analysis.md) | raw+中性对照；形状分流；band-ic；候选池+冗余+密度预估 |
| 2. 策略定制 | [strategy-design.md](strategy-design.md) | `qr config new`；信号层；exit 语义；执行层占位 |
| 3. 回测与优化 | [backtest-optimize.md](backtest-optimize.md) | 协议；**优化纪律**；research；搜参；sensitivity；OOS |
| 4. 质量闸门 | [quality-gates.md](quality-gates.md) | 硬否决：密度 / 退出结构 / 仓位 / 多目标；候选验收后才冻结 |
| 编排索引 | [research-loop.md](research-loop.md) | 分支、停手、反模式 |
| CLI 全表 | [reference.md](reference.md) | 命令与路径 |
| 工程改动 | [../../../agent.md](../../../agent.md) | 四位一体：配置·用例·skill·md |

用户只点某一阶段时，**只读对应文档**；涉及定稿/选格时**必读 quality-gates**。

## 硬性规则

工程与契约（仓库侧）：

1. Agent I/O：`--format json --quiet`；只解析 stdout JSON；日志在 stderr。  
2. 勿编造行情；缺数据停并提示同步 zer0share。勿引入 vnpy。  
3. **事件 CSV 只读**（`workspace/events/**`、`events_ascii/**`）；衍生只写 `workspace/runs/`。  
4. 术语：`entry_intent_date` / `exit_intent_date` 等；CSV `buy_date`/`code` 仅 ingest alias。无隐式 +1，延迟只用 `execution.lag_sessions`。  
5. 策略落盘 YAML：开局 **`qr config new`**；新假设新文件；勿改 `configs/examples/*`；勿通读 `experiments/` 当起点。大表落盘，对话只引路径摘要。

研究纪律（细则见专章；**反模式**见 [research-loop.md](research-loop.md)）：

6. 板块分流（G8）；参考退出日不得留 `exit_intent`（G0/G0b）。  
7. 因子中性对照必做一次；信号默认原始特征。先写 `evaluation` 再搜参。  
8. 只在 train 搜参；一次一类旋钮；禁 `sweep×sensitivity`；`n_trials_assumed` ≥ 格点数（见 backtest-optimize）。  
9. **完整策略 = 信号证据 + 执行证据 + [quality-gates](quality-gates.md)**。`apply-best` 只写候选；未做 sensitivity → `execution_template`。  
10. 决策落盘：`study_id` + 每阶段 `qr study decision`（搜参记否决的更高夏普格）。多窗 OOS；全样本仅披露；stress 差≠机械否决。充分利用 events。达标同报 `mean_invested`。

退出码：`0` ok，`2` 配置，`3` 数据，`4` 门禁 blocked（`promote`），`5` 依赖缺失。  
`pipeline research` 即使 `promotable=false` 也常为 exit `0`——以信封 `status` / `gates` 为准。

## 决策存档（每阶段必做）

```yaml
hypothesis:
  study_id: <topic>_<tag>
```

```bash
qr study decision --study <study_id> --stage <stage> \
  --summary "<一句话>" --rationale "<逻辑>" \
  [--evidence path_or_json] --run <run_id> [--config <yaml>] \
  [--next-action "..."] --format json --quiet
qr analyze report --run <run_id> --format json --quiet
```

| stage | 何时写 |
|-------|--------|
| `factor_analysis` | 因子结论后 |
| `strategy_design` | 实验 YAML 落盘后（含 exit 语义） |
| `backtest_train` | 训练年 research + 闸门结果后 |
| `optimize` / `sweep` / `sensitivity` | 搜参后（含否决格） |
| `backtest_validate` / `holdout` / `holdout_stress` / `full_sample` | 各窗评估后 |

## 默认研究闭环

```
Task Progress:
- [ ] 0. 环境：ping + 列出全部事件年 → validate-events → 澄清 exit 日语义 → evaluation 切分
- [ ] 1. 因子分析（raw+preprocess；shape；区间则 band-ic；密度预估）→ decision
- [ ] 2. qr config new → 写信号（密度自觉）→ strategy_design decision
- [ ] 3. 训练年 research → analyze trades → quality-gates（失败则回 2 或改 risk）
- [ ] 4. （默认）optimize|sweep → apply-best（候选）→ research → analyze trades → 闸门（失败换格/回退）
- [ ] 5. sensitivity（含 cost 维）→ apply-best（候选）→ research → 闸门 → 冻结
- [ ] 6. validate? → holdout final → holdout_stress? → decisions
- [ ] 7. 全样本 research（仅披露）→ full_sample decision → 向用户总结
```

顺序意图：**协议 → 信号 → 诊断/闸门 → 分层搜参 → 闸门 → 冻结 → OOS**。

```bash
qr data ping --format json --quiet
qr data validate-events --csv <all_event_csvs...> --config <base_or_exp.yaml> --format json --quiet
```

## 快捷路径

| 用户意图 | 路径 |
|----------|------|
| 只检查环境/CSV | 步骤 0 |
| 只要因子分析 | factor-analysis.md |
| 只要定策略 YAML | 因子结论已有 → strategy-design.md |
| 只要回测/优化 | 已有 YAML → backtest-optimize.md + **quality-gates.md** |
| 只要示例跑通 | examples research，并声明「未做因子定策略 / 未过闸门」 |
| 只要报告 | `qr analyze report --run ...` |
| 实盘导出 | promote / ops（须闸门 + 用户要求） |

## 配置与产物（速查）

| 区域 | 键 |
|------|-----|
| signals | `filters`、`rank_by`、`composite` |
| portfolio | `starting_cash`、`max_weight`、`max_new_entries_per_day`、行业 cap |
| execution | `price`、`order_validity_sessions`、`entry_filter`、`lag_sessions` |
| risk | `stop_loss`、`take_profit`、`max_hold_sessions`、`exit_priority` |
| gates / evaluation | 笔数、主指标、切分角色 |

中文报告：`workspace/runs/<run_id>/report/research_report_zh.html`。

## 回复用户时

- 写明阶段与**闸门/旁路标签**（完整策略 vs sparse / execution_template）。  
- 因子结论 → 策略假设 → `run_id` → 关键指标 + invested + 退出结构摘要 → 下一动作。  
- 给出配置路径与报告路径。  
- 勿把父项目旧术语或可抄参数配方写入配置。  
