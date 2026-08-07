# qresearch Roadmap

定位：**事件驱动 A 股研究内核 + Agent CLI**（事件 CSV 进 → 因子/策略/回测/门禁 → 可选晋升与信号）。  
不做第二套交易框架；行情继续只经 zer0share。工程纪律见 [agent.md](agent.md)（含配置·用例·skill·md 对齐）；研究闭环见 [`.agents/skills/qresearch/SKILL.md`](.agents/skills/qresearch/SKILL.md)（Cursor 镜像位于 `.cursor/skills/`）。

状态图例：`done` 已有 · `next` 近期优先 · `later` 中期 · `out` 明确不做（或仓外）。

---

## 1. 能力总览（研投步骤对照）

| 研投步骤 | 状态 | 现状摘要 |
|----------|------|----------|
| 行情就绪 | done | `qr data ping`；LocalPro / 环境变量 |
| 事件接入与校验 | done | ingest alias、`validate-events` |
| 事件/标签生产 | out | 仓外扫描器等生成 CSV |
| 因子检验（事件级） | done | Rank IC、compare、shuffle placebo |
| 因子深度诊断 | done | 白黑名单、样本剖面、ICIR、分层收益 |
| 策略 YAML 定义 | done | signals / portfolio / risk / execution / composite |
| 日频回测与约束 | done | GFD/GTD、T+1、涨跌停启发式、成本 |
| WF / 过拟合 / PIT 披露 | done | walk-forward、deflated Sharpe、gates、pit_audit |
| 参数优化 | done | `optimize` 单特征分位；`sweep` 多 filter / `between` 边界；`apply-best` 写新 YAML |
| 执行/风控敏感度 | done | `pipeline sensitivity` 成本×组合旋钮；行含 `mean_invested` |
| 区间因子支路 | done | 分层 `shape`；`factor band-ic`；sweep `.between=`；skill 分流 |
| 报告与 run 对比 | done | 中文报告、`runs compare` |
| 模型晋升 | done | `promote` → `workspace/models`；经济门禁默认开启 |
| 纸面/信号意图 | done（薄） | `ops run` paper/signal |
| 样本治理与去偏 | done（基础） | `sample_profile` + 因子白黑名单；更深前视检查仍 later |
| 场景与成本敏感度 | done | 成本·组合 sensitivity；`evaluation` 多窗 OOS 角色 + 可选超额/IR 门禁 + invested 披露；自动牛熊分类 out |
| 组合层风险约束 | later | 行业/市值暴露、非等权 |
| Paper 对账与监控 | later | 持仓/成交回灌、漂移告警 |
| 实盘 OMS / vnpy | out | 明确边界外 |

---

## 2. 近期（P0）— 已落地

- `sample_profile.json`、因子白黑名单、ICIR / 分层收益
- `hypothesis` → YAML / `meta.json`；结构门禁 vs 经济门禁（默认 min Sharpe / max DD）
- 实现说明：`.cursor/plans/p0_p1_research_hardening.plan.md`

**评估协议（done）**：`evaluation` 切分声明、多窗 OOS 角色、`mean_invested` 主路径披露、可选超额/IR 门禁。  
铁律：实盘不判牛熊；研究用年份/角色说明评估公平性；stress holdout 差≠机械否决。  

**区间因子（done）**：`shape`∈u/inv_u/hump → `between` filter + 带内单调 rank；全样本 IC≈0 不机械否决。  

仍 later：行业/市值分布深化、前视字段清单扩展、自动 regime 分类器（不做年份标签替代）。

---

## 3. 中期（P1）— 部分已落地 / 后续

**已落地**
- `qr pipeline sensitivity`（成本×N、止盈止损网格）
- `signals.composite` zscore 加权合成因子

**仍待**
1. 组合构造：波动率倒数、行业暴露上限
2. 冲击/参与率惩罚；开盘/收盘/lag 矩阵一键对比
3. Optimize 扩到 rank_by / risk（须写回 YAML 再 research）
4. Paper 持仓对账最小闭环

---

## 4. 远期（P2）— 仅在有明确需求时做

- 多策略资本分配与相关性约束  
- 分钟线/逐笔执行（需新数据契约）  
- 在线监控看板、审批流、多用户权限  
- 与外部 OMS 的正式适配层（保持本仓为研究真源）

---

## 5. 明确不做

| 项 | 原因 |
|----|------|
| 引入 vnpy / 第二套回测引擎 | 边界与 [agent.md](agent.md) 硬约束 |
| 仓内实现完整事件扫描器 | 数据与研究解耦；CSV/Parquet 契约即可 |
| 为刷覆盖率堆 e2e | 默认同合成 panel；真实行情标 `e2e_local` |

---

## 6. 工作区与产物（已落地）

```
workspace/
  events/   # 输入事件
  runs/     # 实验 run
  models/   # 晋升包
  cache/    # 行情缓存
```

代码在 `qresearch/`，策略在 `configs/`。新能力优先落盘到 `workspace/runs/<id>/artifacts/`，信封只摘要。

---

## 7. 演进原则

1. **先证据后策略**：因子/样本诊断能力优先于新调参旋钮。  
2. **行为变更必测**：成交、退出、门禁语义变更必须补合成单测。  
3. **配置兼容**：`config/models.py` 新键带安全默认，旧 YAML 可跑。  
4. **Agent 可编排**：新能力优先 CLI + JSON 信封，再补 skill 剧本。

修订记录：随里程碑更新本文件状态列；大改同步 README「Layout / CLI map」。

实现拆解（P0 样本/因子/门禁 + P1 敏感度/合成因子）：见 [`.cursor/plans/p0_p1_research_hardening.plan.md`](.cursor/plans/p0_p1_research_hardening.plan.md)。
