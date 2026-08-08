# Universal Research Kernel Implementation Index

日期：2026-08-08  
状态：拆分为三个顺序迭代

## 使用方式

实现模型一次只读取并执行一个迭代文档。不要把三个迭代合并执行，也不要从后续文档提前实现
类型、配置、CLI 或空 Provider。

严格顺序：

1. [Iteration 1 — Correctness Baseline](2026-08-08-universal-research-kernel-iteration-1-correctness.md)
2. [Iteration 2 — Market Factor Research](2026-08-08-universal-research-kernel-iteration-2-market-research.md)
3. [Iteration 3 — Strategy, Backtest, and CLI](2026-08-08-universal-research-kernel-iteration-3-strategy-cli.md)

每次实现前：

1. 读取仓库根目录 `AGENTS.md`。
2. 完整读取当前迭代文档。
3. 检查前一迭代 completion gate；未通过则停止，不跳级。
4. 按任务顺序做测试驱动实现和独立提交。
5. 只在当前迭代的 Explicit Non-Goals/Global Constraints 内工作。

## 完成后的 Agent 因子分析入口

```text
qr data ping
→ qr research factors
→ qr config new（market universe、候选 zer0factor refs、train/validate/holdout）
→ qr research materialize（冻结 universe、因子、标签和 split）
→ qr research evaluate（内部调用 zer0factor EvaluationService，仅 train）
→ Agent 读取 zer0factor summary/report/daily_ic/quantile_returns
→ Agent 读取 qrsearch train-only factor_redundancy
→ qr study decision --stage factor_analysis
→ 策略设计、OOS 回测、质量门禁
```

`qr research evaluate` 不是 qrsearch 自建分析器：它用 FrozenSnapshotStorage 和 TrainUniversePro 把
qrsearch 的冻结快照与 train membership 注入 zer0factor。validate/holdout 不参与候选因子和方向选择。

## 三个迭代的边界

### Iteration 1

定义事件研究的规范正确性基线：contract golden、Spearman ties/NaN、zer0share 历史
`up_limit/down_limit`、缓存 fingerprint。没有新研究内核。

### Iteration 2

建立唯一研究数据链的最小纵向切片：zer0share market SampleSet、zer0factor 只读快照、固定 horizon 标签、
时间角色和 zer0factor train-only 因子分析，并暴露 `qr research factors/materialize/evaluate`。同时删除
event/CSV ingest、qrsearch 旧因子分析器和旧研究命令；
本迭代不提供策略回测。

### Iteration 3

把冻结的 market dataset 接入唯一一套 signal/backtest，重新提供 market-only 的 `pipeline research`、
optimize/sweep/sensitivity，并补报告、promote 和配置脚手架。

## Deferred Backlog

以下能力不属于三个迭代。没有单独设计和用户批准前，不创建空类、空文件、配置枚举或成功信封：

- index/custom/hybrid SampleProvider
- 全局不可变、内容寻址的 zer0factor FactorArtifact
- 财务数据 revision/vintage 与完整 bitemporal join
- HAC/Newey-West、block bootstrap、FDR、PBO、CPCV
- 通用 ResearchStage/DAG、跨阶段 cache invalidation
- 分钟线、排队、冲击、参与率和容量模型
- theoretical/tradable/total-return 标签矩阵
- 多策略共享现金 book
- 针对真实 profiling 结果之外的性能重构

若用户请求 deferred 能力，先建立新的 dated design + implementation plan，不直接追加到 Iteration 3。

## Global Product Constraints

- zer0share 是唯一原始市场数据来源。
- zer0factor 是唯一因子公式、因子值、预处理和通用因子分析来源；qrsearch 仅补充 train-only 跨因子冗余矩阵。
- 三个迭代均把 zer0share、zer0factor 当作稳定的只读外部依赖；禁止修改这两个仓库的代码、配置、
  数据布局或测试。API 差异、路径解析、快照、校验和错误翻译全部在 qrsearch 的 provider/adapter 中实现。
- 若现有上游能力不足，当前迭代必须明确失败并给出缺失能力，不得用空实现冒充成功；只有无法在
  qrsearch 安全适配时，另写上游变更提案并等待用户明确批准，不得顺手修改上游。
- 这是一次性切换，不要求向后兼容。新实现落地时删除旧配置字段、旧 CLI 参数、旧 adapter、旧分支
  和仅服务旧行为的测试；禁止保留 deprecated alias、双写、双读、mode 开关或 fallback。
- 不引入 vnpy。
- Agent I/O 使用 `--format json --quiet` 和退出码 0/2/3/4/5。
- 最终产品不读取 `workspace/events/**`、`workspace/events_ascii/**`；删除代码时也不得修改或删除这些历史数据。
- 行为变化同步 config、tests、Skill、README；仅内部重构不为文档而文档。
- 不支持的能力明确返回 config/dependency error；禁止用空数据冒充成功。
