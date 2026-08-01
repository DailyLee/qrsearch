# Research loop — 编排索引

Agent 判断；CLI 只计算。主入口 [SKILL.md](SKILL.md)。  
细则按大类拆分，避免单文件过载：

| 专章 | 内容 |
|------|------|
| [factor-analysis.md](factor-analysis.md) | 读 IC、选因子、写假设 |
| [strategy-design.md](strategy-design.md) | 写 YAML；信号 vs 执行/风控 |
| [backtest-optimize.md](backtest-optimize.md) | research / sensitivity / optimize / holdout |
| [reference.md](reference.md) | CLI 与目录 |

## Roles

| Layer | Responsibility |
|-------|----------------|
| CLI (`qr`) | validate, IC, backtest, optimize, report, promote, ops |
| Skill (you) | 解读证据、写 YAML、选下一阶段、停手 |
| User | 目标、数据区间、是否 promote / force |

## 分支（何时进哪一章）

```
环境 ok?
  no  → 停，提示数据/依赖
  yes → 有因子结论?
          no  → factor-analysis.md
          yes → 有实验 YAML（信号层）?
                  no  → strategy-design.md（先写 signals）
                  yes → 执行/风控已 sensitivity 定稿?
                          no  → backtest-optimize.md §B（信号冻结后必做）
                          yes → research(train) / holdout / 全样本终测 / 对比
                                 → 每阶段 qr study decision 落盘
                                 → 改进? 保留 champion；默认 optimize 一轮（仅训练年；跳过须写理由）
                                 → 无改进: 只改一类旋钮 → 新 YAML → 再 research
                                 → 3 轮无改进 → STOP
```

## 停手条件

1. 用户只要 smoke / 单次回测  
2. Champion 达标且再调收益有限  
3. 连续 3 轮 research 无改进  
4. 数据/依赖错误（exit 3/5）  
5. 用户说停  

停手时报告：champion `run_id` + 配置；因子理由；指标；执行层是否定稿；为何不 promote；续跑命令。

## Anti-patterns（速查）

- 未做因子却称「因子驱动」  
- **机械只取 IC Top2 定策略**（应建候选池 + 冗余检查 + filter/rank/composite）  
- 只改 signals、执行层抄模板却称完整策略  
- 口头改参不写 YAML / 覆盖历史实验文件  
- holdout 参与 optimize 或看完 holdout 再调参  
- 单年全样本 optimize 当可晋升证据  
- 一轮同时拧多类旋钮（无法归因）  
- `--force` promote「收尾」  
