# qresearch Roadmap

定位：**事件驱动 A 股研究内核 + Agent CLI**（事件 CSV 进 → 因子/策略/回测/门禁 → 可选晋升与信号）。  
不做第二套交易框架；行情继续只经 zer0share。工程纪律见 [agent.md](agent.md)；研究闭环见 [`.cursor/skills/qresearch/SKILL.md`](.cursor/skills/qresearch/SKILL.md)。

状态图例：`done` 已有 · `next` 近期优先 · `later` 中期 · `out` 明确不做（或仓外）。

---

## 1. 能力总览（研投步骤对照）

| 研投步骤 | 状态 | 现状摘要 |
|----------|------|----------|
| 行情就绪 | done | `qr data ping`；LocalPro / 环境变量 |
| 事件接入与校验 | done | ingest alias、`validate-events` |
| 事件/标签生产 | out | 仓外扫描器等生成 CSV |
| 因子检验（事件级） | done | Rank IC、compare、shuffle placebo |
| 因子深度诊断 | next | ICIR、分层收益、中性/衰减等 |
| 策略 YAML 定义 | done | signals / portfolio / risk / execution |
| 日频回测与约束 | done | GFD/GTD、T+1、涨跌停启发式、成本 |
| WF / 过拟合 / PIT 披露 | done | walk-forward、deflated Sharpe、gates、pit_audit |
| 参数优化 | done（窄） | Optuna 偏特征 between-filter |
| 报告与 run 对比 | done | 中文报告、`runs compare` |
| 模型晋升 | done | `promote` → `workspace/models` |
| 纸面/信号意图 | done（薄） | `ops run` paper/signal |
| 样本治理与去偏 | next | 分层、前视/存活检查清单化 |
| 场景与成本敏感度 | next | 熊市段、成本倍率扫描 |
| 组合层风险约束 | later | 行业/市值暴露、非等权 |
| Paper 对账与监控 | later | 持仓/成交回灌、漂移告警 |
| 实盘 OMS / vnpy | out | 明确边界外 |

---

## 2. 近期（P0）— 把「研究结论」做硬

目标：同一事件策略在样本与稳健性上更可信，Agent 少踩坑。

1. **样本与 PIT 治理**
   - 事件集摘要：年份/行业/市值分布、重复标的、持有重叠
   - 扩展 `pit_audit`：常见前视字段、调整口径检查清单（可 warn/fail）
   - CLI 或 research 产物中落盘 `sample_profile.json`

2. **因子诊断加深**
   - 分层收益（quantile）、IC 序列与 ICIR
   - 特征衰减（多 horizon 已有则补稳定性摘要）
   - 报告「因子分析」章节引用新表，而非仅单点 Rank IC

3. **稳健性扫描（轻量）**
   - 成本倍率 / 滑点敏感度（小网格，写入 artifacts）
   - 按年份或 regime 切片的绩效表（可复用 WF fold 展示）

4. **研究台账约定（文档 + 轻量元数据）**
   - `configs/experiments/` 命名与假设字段（YAML 顶层 `hypothesis` 注释或键）
   - run `meta.json` 记录假设摘要与父 run / n_trials

验收：`pytest` 覆盖新纯逻辑；skill/README 同步命令与产物路径。

---

## 3. 中期（P1）— 组合与执行更贴近实盘假设

1. **组合构造选项**
   - 在等权之外：可选波动率倒数、上限约束组合
   - 可选简单行业暴露上限（依赖事件或行情侧行业字段）

2. **执行模型升级（仍日线）**
   - 可配置冲击/参与率惩罚（接现有 capacity 启发式）
   - 开盘/收盘/次日开盘矩阵的一键对比 run

3. **Optimize 范围**
   - 在过拟合门禁下，允许对 `rank_by` / 少量 risk 参数搜索
   - 强制写出 `best_params` → 新 YAML → 再 `pipeline research`

4. **Paper 闭环（最小）**
   - `ops` 持仓 state 读写规范与示例
   - 日终：意图 vs 假定成交 vs 账户快照的差异报告

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
