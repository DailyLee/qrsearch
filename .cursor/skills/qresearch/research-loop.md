# Research loop — 编排索引

Agent 判断；CLI 只计算。主入口 [SKILL.md](SKILL.md)。  
细则按大类拆分，避免单文件过载：

| 专章 | 内容 |
|------|------|
| [factor-analysis.md](factor-analysis.md) | raw+中性对照、选因子、写假设 |
| [strategy-design.md](strategy-design.md) | `qr config new`；信号 vs 执行/风控；exit 语义 |
| [backtest-optimize.md](backtest-optimize.md) | 协议 / 优化纪律 / research / 搜参 / sensitivity / OOS |
| [quality-gates.md](quality-gates.md) | **硬否决**：密度、退出结构、仓位、多目标；候选验收后才冻结 |
| [reference.md](reference.md) | CLI 与目录 |

**反模式全文只在本文件维护**；专章不重复罗列，只链到此处或 quality-gates。

## Roles

| Layer | Responsibility |
|-------|----------------|
| CLI (`qr`) | validate, IC, backtest, optimize, report, promote, ops |
| Skill (you) | 解读证据、写 YAML、过质量闸门、选下一阶段、停手 |
| User | 目标、数据语义、是否 promote / force |

## 分支（何时进哪一章）

```
环境 ok?（含 exit 日语义：参考退出 → 无 exit_intent）
  no  → 停，提示数据/依赖/语义
  yes → 有 evaluation 协议?
          no  → 先写 evaluation / statement（backtest-optimize 评估协议）
          yes → 有因子结论（preprocess 对照；区间看 shape/band-ic）?
                  no  → factor-analysis.md
                  yes → 有实验 YAML（信号层，config new 脚手架）?
                          no  → strategy-design.md
                          yes → 有训练年 research?
                                  no  → backtest-optimize §A → analyze trades → quality-gates
                                  yes → 闸门失败?
                                          density/signal → 回信号层
                                          exit_structure → sensitivity 或改 risk
                                          通过 → 信号已搜（optimize|sweep 或跳过）?
                                                  no  → §C → apply-best 候选 → §A → 闸门
                                                  yes → sensitivity 定稿?
                                                          no  → §B → apply-best 候选 → §A → 闸门
                                                          yes → 冻结 champion（非旁路）?
                                                                  → validate? → holdout → stress?
                                                                  → full disclose → STOP
                                                                  （stress 差只归因；3 轮无改进 → STOP）
```

**铁律**：`best_value` 最高 ≠ 可冻结；流程为 `apply-best`（候选）→ research → [quality-gates.md](quality-gates.md)。

## 停手条件

1. 用户只要 smoke / 单次回测  
2. Champion 达标且再调收益有限（须已过闸门，非旁路冒充）  
3. 连续 3 轮 research 无改进  
4. 数据/依赖错误（exit 3/5）  
5. 用户说停  
6. G0b：参考退出语义未澄清且 `exit_intent` 主导退出  

停手时报告：champion 或旁路标签；`run_id` + 配置；因子理由；指标 + invested + 退出结构摘要；执行层是否定稿；为何不 promote；续跑命令。

## Anti-patterns（全文唯一清单）

### 因子 / 信号证据

- 未做因子却称「因子驱动」；照抄 examples / 历史实验 / 记忆中的 filter·rank  
- 只跑 raw IC、跳过 preprocess 中性对照（缺字段须在 decision 写明）  
- 机械只取 IC Top2；无冗余分析；默认信号改用 `__prep`  
- 全样本 IC≈0 就否决 U/倒U/单峰（应走区间支路 + band-ic）  
- 用 holdout 调 between 边界后再报带内 IC  
- 区间因子塞进 `optimize --keep-frac` 或当唯一 `rank_by`

### 配置 / 落盘

- 开局不用 `qr config new`；临时 `.py` / 手抄整份 YAML  
- 通读/抄 `configs/experiments/` 当新研究起点  
- 口头改参不写 YAML；改原始 `workspace/events*`  
- skill / 对话粘贴可抄的止损止盈 / 特征阈值配方当「默认策略」

### 样本与优化

- 未写评估协议就网格搜参  
- holdout / validate / stress 参与搜参，或看完 OOS 再改参 / 改网格边界  
- 用 stress 牛市绝对收益机械否决（须写角色与归因）  
- 单年全样本 optimize 当可晋升证据；全样本当调参/晋升结论  
- 未充分利用 events（无故丢年；full 不覆盖本次全部可用年）  
- 为搜参改引擎/自写脚本；`sweep × sensitivity` 联乘  
- 一轮同时拧多类旋钮；未冻结上一层就搜下一层  
- 为冲指标改 `costs`

### 定稿 / 闸门（细则见 quality-gates）

- 信封夏普最高 → `apply-best` → 跳过 research/闸门就称定稿  
- 纸面盈亏比：TP 几乎不触发仍称止盈止损定稿  
- 稀疏刷夏普：过稀硬过滤 + 低 invested，有年化目标时标 champion  
- 参考退出日仍留 `exit_intent`  
- 多目标偷换：用户要夏普+年化，只按夏普选格  
- 只改 signals、执行层抄模板却称完整策略  
- 达标不报 `mean_invested`；`--force` promote「收尾」  
- 把因子行业中性当成组合分散，或无动机乱填行业 cap  
