# qrsearch 通用量化研究内核设计

状态：Proposed — 已补充量化正确性评审  
日期：2026-08-08  
范围：`qrsearch` 通用化，以及与 `zer0share`、`zer0factor` 的稳定集成边界

## 0. 交付解释

本文描述当前目标架构。2026-08-08 获批实现范围已拆分为三个顺序迭代，执行时以下列索引和
对应迭代文档为权威：

- [`Iteration 1 — Correctness Baseline`](../plans/2026-08-08-universal-research-kernel-iteration-1-correctness.md)
- [`Iteration 2 — Market Factor Research`](../plans/2026-08-08-universal-research-kernel-iteration-2-market-research.md)
- [`Iteration 3 — Strategy, Backtest, and CLI`](../plans/2026-08-08-universal-research-kernel-iteration-3-strategy-cli.md)

三个迭代只交付 `zer0share PIT universe + zer0factor` 这一条 market 链路，并一次性删除 event/CSV 入口，
不保留版本兼容代码。index/custom/hybrid/event Provider、全局不可变
FactorArtifact、revision/vintage、HAC/FDR/PBO/CPCV、通用 DAG/cache 和更细执行模型属于长期
目标，不是三个迭代的完成条件。实现 Agent 不得为这些延期能力创建空实现；后续需单独设计和计划。

若本文长期验收标准与某个迭代的 Explicit Non-Goals 冲突，以当前迭代文档为准。

## 1. 背景

`qrsearch` 当前是一套事件驱动 A 股研究内核。主流程从事件 CSV 读取
`instrument`、`decision_date`、`entry_intent_date`、`exit_intent_date` 和
`features.*`，随后完成因子诊断、信号构造、回测、Walk-forward、门禁、报告与晋升。

该实现已经形成完整研究闭环，但存在结构性限制：

- 研究样本被等同于事件 CSV，难以复用到全市场截面、指数成分和自定义股票池。
- 因子值、样本定义和事件日期耦合在一张表中，数据来源与研究协议不够独立。
- 因子预处理和评估同时承担通用统计与事件条件化统计，口径容易混淆。
- 新因子可能在扫描器、CSV、`qrsearch` 和 `zer0factor` 之间重复实现。
- run 产物尚不能完整追溯到因子代码版本、因子数据版本和可用时点规则。

目标不是扩展一个更大的事件流水线，而是建立通用研究内核：

```text
ResearchDataset = SampleSet × FeatureSnapshot × LabelSet
```

SampleSet 固定表示 zer0share 动态 universe 的股票×交易日观察。因子统一由 `zer0factor` 生成、预处理、
版本化和存储；`qrsearch` 负责样本、标签、切分、截面评估、策略、回测和实验治理。

## 2. 目标

1. 使用 zer0share 的逐日 PIT universe 支持全市场或已物化 universe 的截面研究。
2. 让 `zer0factor` 成为因子公式、因子值、处理变体和因子版本的唯一真源。
3. 复用 `qrsearch` 已有的交易约束、Walk-forward、门禁和报告能力，删除事件 CSV 数据入口。
4. 所有研究输入都满足 point-in-time，可解释数据何时观察、何时真正可用。
5. 因子评估、策略构造和组合回测分层，避免用单一 IC 直接推导交易参数。
6. 每个正式 run 都能由不可变配置、数据指纹、因子版本和代码版本复现。
7. 新内核落地后删除旧事件 CLI 参数、旧 YAML 字段和旧编排；只保留对 Agent I/O 有意义的新 JSON 信封契约。

## 3. 非目标

- 不在 `qrsearch` 中实现第二套行情、基本面或因子存储系统。
- 不把 `zer0factor` 的 Alphalens 日频评估结果当作 qrsearch 截面/组合评估的等价替代。
- 不在本次改造中引入 vnpy、OMS、分钟线或实盘交易框架。
- 不建设双内核、兼容适配器、配置迁移器或弃用周期；本项目允许 breaking change。
- 不把因子计算塞进信号或回测循环。
- 不承诺三个项目立即合并为 monorepo；当前保持同目录独立仓库。
- 三个当前迭代不修改 zer0share 或 zer0factor；跨仓缺口优先由 qrsearch 的只读 adapter 解决。

## 4. 系统所有权

### 4.1 zer0share

唯一负责：

- 原始行情、复权因子、基本面、行业、指数成分和交易日历。
- 原始数据查询和数据集指纹。
- 原始字段的 point-in-time 语义。

不负责因子公式、研究样本、策略和回测。

### 4.2 zer0factor

唯一负责：

- `FactorSpec`、因子依赖、因子计算和 FactorFamily。
- 原始、标准化、中性化等明确命名的因子变体。
- 因子值存储、版本 manifest、覆盖率和计算 lineage。
- 通过 `EvaluationService` 提供全市场日频 IC、分层收益、单调性、换手、基础组合诊断与候选因子预筛。

不负责研究样本切分、策略参数搜索和最终组合晋升。

### 4.3 qrsearch

唯一负责：

- `SampleSet` 和动态 universe 定义。
- `LabelSpec`、标签物化、训练/OOS 切分、purge 和 embargo。
- train/validate/holdout 研究协议，以及 zer0factor screening 输入的 PIT/角色审计。
- 信号选择、组合构造、成本、成交限制、风险、回测和 Walk-forward。
- 实验注册、假设、试验次数、质量门禁、报告和模型晋升。

`qrsearch` 只引用因子，不拥有因子公式、预处理或通用评价指标实现。唯一允许的补充因子诊断是 train-only
跨因子冗余矩阵，因为当前 zer0factor EvaluationService 尚未提供该产物。

## 5. 核心领域模型

### 5.1 ObservationKey

唯一标识一次可研究观察：

```python
@dataclass(frozen=True)
class ObservationKey:
    sample_id: str
    ts_code: str
    asof_time: datetime
    effective_time: datetime
```

- `asof_time`：研究决策允许使用的信息截止时点。
- `effective_time`：该观察进入信号或组合的时点。
- 两者禁止隐式相等。
- 日频阶段可以规范化到交易时段标签，但领域模型保留时间而非只有日期。

### 5.2 SampleSet

`SampleSet` 只回答“研究哪些观察”，不携带因子公式和未来收益：

当前只有一个具体实现，不建立 Provider registry：

```python
class MarketSampleProvider:
    def materialize(self, config: SampleConfig) -> SampleSet: ...
```

它只生成某个 zer0share 动态 universe 的全部日频观察。index/custom/event 若未来确有需求，单独设计，
不预留枚举、空 Provider 或分派层。

`SampleSet` 最少包含：`sample_id, ts_code, asof_time, effective_time, sample_weight`。

### 5.3 FeatureRef

明确引用一个不可变因子版本：

```python
@dataclass(frozen=True)
class FeatureRef:
    name: str
    version: str
    variant: Literal["raw", "standardized", "neutralized"]
    additional_lag_sessions: int = 0
```

- `variant` 是因子身份的一部分，禁止在 `qrsearch` 中静默标准化。
- 因子的最早可用时点由不可变 manifest 的 `availability_rule` 决定；研究配置只能用
  `additional_lag_sessions` 增加更保守的延迟，禁止缩短 manifest 延迟。
- `version` 必须解析到不可变 manifest，而不是可变的“latest”路径。

### 5.4 FeatureSnapshot

`FeatureProvider` 把 `FeatureRef` 对齐到 `SampleSet`：

```python
class FeatureProvider(Protocol):
    def materialize(
        self,
        samples: SampleSet,
        features: tuple[FeatureRef, ...],
    ) -> FeatureSnapshot: ...
```

Snapshot 包含：

- 标准观察键和因子列。
- 每个因子的 resolved version、变体和 availability 规则。
- 数据指纹、覆盖率、缺失率、重复键检查和时间范围。
- 物化时间与生成代码版本。

Snapshot 一旦进入正式 run 就不可变，回测期间禁止重新查询因子 latest 值。

Feature 对齐不是日期等值连接。Provider 必须先解析 manifest 的 `observed_at`、
`available_at`、`valid_until` 和 revision/vintage，再按以下 as-of 条件广播到完整观察键：

```text
factor.ts_code = sample.ts_code
AND factor.available_at <= sample.asof_time
AND (factor.valid_until IS NULL OR factor.valid_until > sample.asof_time)
AND factor.version = requested_version
```

输出必须保留 `sample_id, ts_code, asof_time, effective_time`，并证明每个 sample/feature
至多解析到一个值。同一股票同一时点的多个事件可以共享因子值，但不得产生多对多行膨胀。

### 5.5 LabelSpec 和 LabelSet

标签与因子完全分离：

```python
@dataclass(frozen=True)
class LabelSpec:
    name: str
    entry_price: Literal["open", "close"]
    entry_lag_sessions: int
    horizon_sessions: int
    exit_price: Literal["open", "close"]
    benchmark: str | None
    return_kind: Literal["price", "total", "excess"]
    execution_mode: Literal["theoretical", "tradable"]
    missing_price_policy: Literal["censor", "defer", "conservative_mark"]
```

`LabelProvider` 只能读取 `asof_time` 之后的价格来生成标签，但生成后的标签不能进入特征处理。
每条标签还要保存其信息区间 `[label_start, label_end]`，供 purge 和 embargo 使用。
标签不得静默删除停牌、退市、涨跌停不可成交或样本尾部截断的观察；必须保存
`label_status` / `censor_reason`，分别披露理论收益覆盖率和可交易收益覆盖率。

### 5.6 SplitManifest

切分结果必须物化并冻结，而不是只在 YAML 中声明年份：

```python
@dataclass(frozen=True)
class FoldManifest:
    fold_id: str
    train_ids: tuple[str, ...]
    validate_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    purged_ids: tuple[str, ...]
    embargoed_ids: tuple[str, ...]
    train_interval: tuple[datetime, datetime]
    test_interval: tuple[datetime, datetime]

@dataclass(frozen=True)
class SplitManifest:
    folds: tuple[FoldManifest, ...]
    final_holdout_ids: tuple[str, ...]
    stress_ids: tuple[str, ...]
    purge_sessions: int
    embargo_sessions: int
    max_feature_lookback_sessions: int
```

规则：

- 时间序列禁止随机打乱切分。
- 标签区间与测试区间重叠的训练观察必须 purge。
- purge 使用每条标签的真实 `[label_start, label_end]`，不能只按固定天数截断。
- embargo 至少覆盖研究协议声明的残余依赖窗口；每个 fold 保存被排除 ID 和原因。
- 因子回看窗口、训练型预处理拟合窗口和 universe 生效窗口都进入切分审计。
- holdout final 只做一次正式结论，不能参与因子选择和参数调优。

### 5.7 EvaluationProtocol

通用因子评估由 zer0factor EvaluationService 执行；qrsearch 负责保证输入来自冻结 snapshot 且 membership
只包含 train。评估协议必须声明统计问题，结果不可混为同一排名：

- `cross_sectional`：按交易日计算截面 Rank IC，再做时间聚合。
- `portfolio`：将信号送入组合和执行模型后评估经济结果。

qrsearch 不重新计算 pooled 或 date-wise IC；直接消费 zer0factor 的 daily_ic、summary 和 report。

通用因子评估最少输出：

- 覆盖率、缺失率、样本数和有效日期数。
- Rank IC 均值、标准差、ICIR、方向胜率和滚动稳定性。
- 分层收益、单调性、top-bottom spread。
- 因子自相关、换手代理、行业/市值暴露。
- 不同 universe、市场阶段和样本模式的稳定性。
- IC 的 HAC/Newey-West 统计或按日期 block bootstrap；事件样本按日期/股票处理聚类相关。
- 多重试验数量、选择过程、FDR/等价校正和 Deflated Sharpe；大规模搜索还要披露
  PBO/CPCV 或明确标记未评估。
- 有效样本量和聚类数，不能只披露原始行数。

### 5.8 StrategySpec

策略公共配置不依赖事件专属日期字段，并复用现有最小对象：

- 日频再平衡：当前唯一频率，不增加 `RebalanceSpec` 枚举。
- `SelectionSpec`：filters、rank、composite 和 top-k。
- `PortfolioSpec`：权重、持仓数、行业和个股上限。
- `risk.max_hold_sessions`：当前唯一固定持有周期；止损止盈继续使用既有 risk 字段。
- `ExecutionSpec`：信号延迟、成交价、订单有效期、成本和涨跌停约束。

执行约束必须从 point-in-time 证券状态解析，不能用单一固定比例代表全部 A 股：至少覆盖
10%/20% 板块、ST、制度切换、新股特殊交易期、停牌和退市。只有日线 OHLC 时，若同日
同时触发止盈和止损，必须使用预声明的保守路径或输出收益上下界，不能把未知日内路径
当成确定成交顺序。

Market strategy adapter 可以在内存中生成底层 signal/backtest 暂时需要的
`entry_intent_date / exit_intent_date`，但这些列不是公共配置或 CSV 契约。

## 6. 数据流

```text
ResearchConfig
      │
      ├── MarketSampleProvider ─────────> SampleSet
      │                                      │
      ├── Zer0FactorFeatureProvider ─────> FeatureSnapshot
      │                                      │
      ├── LabelProvider ──────────────────> LabelSet
      │                                      │
      └── Splitter ───────────────────────> SplitManifest
                                             │
                                   ResearchDataset
                                             │
                    ┌────────────────────────┼───────────────────────┐
                    │                        │                       │
       Zer0FactorEvaluationService    SignalBuilder         PortfolioBacktest
                    │                        │                       │
                    └────────────────────────┴───────────────────────┘
                                             │
                                      Run artifacts/report
```

主 pipeline 使用显式线性编排，不建立通用 DAG：

1. `materialize_samples`
2. `materialize_features`
3. `materialize_labels`
4. `build_split_manifest`
5. `evaluate_features_with_zer0factor`
6. `build_signals`
7. `run_backtest`
8. `evaluate_gates`
9. `write_report`

每个阶段只读取前序不可变产物。EvaluationService 通过 qrsearch 的 FrozenSnapshotStorage 和
TrainUniversePro adapter 读取冻结因子与 train membership，不直接读取可变 FactorStorage 或全期 universe。

### 6.1 Agent 因子分析路径

```text
qr data ping
  → qr research factors（列出 FactorStorage 可读取因子）
  → qr config new（填写 market universe、时间角色、候选 refs）
  → qr research materialize（冻结 SampleSet/FeatureSnapshot/LabelSet）
  → qr research evaluate
      → FrozenSnapshotStorage
      → TrainUniversePro
      → zer0factor.services.EvaluationService
      → summary/report/daily_ic/quantile_returns
      → qrsearch train-only factor_redundancy
  → Agent 读取证据并写 factor_analysis decision
  → 策略设计 → market OOS 回测 → quality gates
```

raw 与中性化对照必须引用 zer0factor 已物化的不同 factor names；qrsearch 不现场拟合预处理。Agent 不读取
validate/holdout 来选择因子方向、分位数或候选池，zer0factor screening 产物也不得标记为 OOS/promotable。

## 7. Point-in-time 规则

以下规则属于硬约束：

1. 原始字段同时声明 observation time 和 availability time。
2. 因子计算只允许读取 `asof_time` 可见的数据。
3. 收盘后因子用于次日开盘，必须设置至少一个合适的 session lag。
4. 基本面信息按真实发布日期连接，不能按报告期直接前填。
5. 动态 universe 使用当时成分，禁止用当前上市股票回填历史。
6. 复权模式必须进入 factor manifest 和 run lineage。
7. 因子回看窗口必须在物化前扩展，不能让研究起点前几期静默缺失。
8. 横截面预处理仅使用当日可见股票；训练型预处理参数只能由训练区间拟合。
9. 全市场和指数样本必须使用逐日有效 universe；禁止以当前 `list_status=L` 股票回填历史。
10. 财务和可修订数据必须保存公告时间与 vintage；仅有当前修订值的数据不得标 PIT-safe。
11. 所有因子只从 zer0factor 只读快照连接；生产代码不读取 event CSV 或 inline `features.*`。

## 8. 配置设计

新配置按职责拆分：

```yaml
data:
  calendar: cn_stock
  adjustment: qfq_pit

sample:
  universe: univ_research_base
  start_date: YYYY-MM-DD
  end_date: YYYY-MM-DD

features:
  provider: zer0factor
  refs:
    - name: ret20_0
      version: sha256:6f1c9bb3a19e0d8dc6d9cf6d8eb656643dde532780982e6a2f28b66a91c13d7a
      variant: neutralized
      additional_lag_sessions: 1

label:
  name: open_t1_to_open_t6
  entry_price: open
  entry_lag_sessions: 1
  horizon_sessions: 5
  exit_price: open
  benchmark: 000852.SH

split:
  method: walk_forward
  purge_sessions: 5
  embargo_sessions: 5

evaluation:
  protocol: cross_sectional
  primary_metric: excess

strategy:
  rebalance:
    kind: daily
  selection:
    filters: []
    rank_by: []
```

配置模型使用 `extra="forbid"`。旧 mode、kind、source(s)、event/CSV、inline feature provider 和其他已删除字段直接
返回配置错误；不提供 alias、自动迁移或 fallback。`resolved_config.yaml` 只记录新模型的默认值展开结果。

## 9. Run 产物与 lineage

每个 run 最少写入：

```text
workspace/runs/{run_id}/
├── config_snapshot.yaml
├── resolved_config.yaml
├── meta.json
├── artifacts/
│   ├── sample_set.parquet
│   ├── feature_snapshot.parquet
│   ├── feature_manifest.json
│   ├── label_set.parquet
│   ├── dataset.parquet
│   ├── split_summary.json
│   ├── factor_screening_manifest.json
│   ├── factor_redundancy.parquet
│   ├── zer0factor_evaluation/
│   ├── metrics.json
│   └── pit_audit.json
└── report/
```

`meta.json` 必须记录：

- `qrsearch`、`zer0factor`、`zer0share` 的 commit SHA 或发布版本。
- factor manifest hash 和数据指纹。
- SampleSet、LabelSpec、SplitManifest 摘要。
- 所有候选因子和参数试验数量。
- 命令、开始/结束时间、运行环境和随机种子。
- 每阶段 cache key、artifact 内容哈希及其输入依赖；缓存命中不得把真实数据指纹替换为
  `cache_hit` 等状态字符串。

## 10. 一次性切换策略

开发按迭代推进，但产品只交付一套最终行为：

1. Iteration 1 已用规范合成样本修正旧引擎的统计和执行正确性；该 contract 不要求继续保留 event API。
2. Iteration 2 建立唯一 MarketSampleProvider 和 market 研究数据集，同时删除旧配置字段、`--csv` 参数、
   inline factor 路径、旧 dispatcher/adapter 和仅服务这些代码的测试；该阶段只提供 materialize/evaluate。
3. Iteration 3 基于冻结 market dataset 重新提供 `pipeline research` 和优化/敏感性命令。
4. 使用 `rg` 和 CLI 拒绝用例证明没有静默 fallback；不提供 migrate 命令或弃用周期。

全过程只有 zer0factor 一个因子真源；qrsearch 对 FactorStorage 始终只读。

## 11. 测试策略

### 11.1 契约测试

- ObservationKey 唯一性和时区/交易时段规范化。
- SampleProvider 输出 schema、重复键和动态 universe PIT。
- Zer0FactorFeatureProvider 版本解析、lag join、缺失和覆盖率。
- zer0factor EvaluationService adapter 的冻结存储、train-only universe、请求映射和 artifact 审计。
- LabelProvider 价格口径、未来区间和停牌处理。
- Splitter 的 purge/embargo 不变量。

### 11.2 合成数据测试

- 人工构造未来才发布的因子值，验证不会提前连接。
- 人工构造指数调仓，验证历史 universe 不使用未来成分。
- 人工构造标签重叠，验证训练样本被 purge。
- 覆盖 market universe 调入、调出、退市、停牌和末端无下一交易日。

### 11.3 契约回归测试

- market 满足统一观察键、数据 lineage、标签和回测契约。
- Iteration 1 的规范结果用于确认正确性修复已经落地；Iteration 3 删除对应 event contract 测试。
- CLI JSON 信封和退出码满足新 Agent 契约；已删除参数和字段必须明确失败。

### 11.4 本地集成测试

- 使用本机 zer0share 数据和少量已计算因子运行端到端 smoke test。
- 标记为 `e2e_local`，默认单元测试不依赖本地全量数据。

## 12. 风险与缓解

### 12.1 zer0factor 版本能力不足

当前存储偏向按日期覆盖，版本 manifest、availability lag 和自动回看预热仍需增强。
三个迭代不修改 zer0factor。qrsearch 在 run 开始时批量读取、复制到 run 目录、计算内容哈希并在之后只读
该快照；读取期间检测到源分区变化则失败。这个 run-level snapshot 足够支撑当前 market 交付，
但不冒充全局 FactorArtifact。需要跨 run 的不可变发布时另立上游提案并取得用户批准。

EvaluationService 默认会自行读取 FactorStorage 和 universe。qrsearch 禁止直接采用默认依赖组合，必须注入
FrozenSnapshotStorage 与 TrainUniversePro；其 `open_t1`/Alphalens 收益只用于 train screening，正式收益、
成交约束和 OOS 结论仍以 qrsearch LabelSet 与 backtest 为准。

### 12.2 Pandas 与 Polars 边界

跨项目使用 Arrow/Parquet 作为稳定边界。转换集中在 Provider adapter，业务层禁止反复转换。

### 12.3 评估口径漂移

报告必须明确显示 protocol、sample kind、label 和 universe。不同 protocol 的指标不进入同一默认排行榜。

### 12.4 一次性切换风险

通过三个顺序迭代、规范 contract 和切换前完整测试降低风险；但最终代码只保留新入口。不得以降低切换
风险为由留下双路由、deprecated alias 或 fallback。

### 12.5 当前 FactorStorage 可变且无版本 manifest

当前 `zer0factor.storage.FactorStorage` 会按因子名覆盖日期分区，registry 只有名称与更新时间，
无法解析长期设计要求的不可变版本。未来若获单独批准，可在 `zer0factor` 实现内容寻址、只追加的 FactorArtifact：
manifest 至少包含 FactorSpec、因子代码哈希、处理 profile、原始数据快照、复权、universe、
availability、revision/vintage、依赖版本和分区文件哈希；物化完成并校验后再原子发布。
当前三个迭代不做该上游修改，使用 12.1 的 qrsearch run-level snapshot 和严格门禁。

### 12.6 当前全市场入口存在幸存者偏差

当前 `zer0factor` 的 `universe="all"` 通过今天仍上市的股票列表构建历史面板，会遗漏退市股票。
通用内核必须从 `zer0share` 的逐日证券主数据生成 market SampleSet，并对上市、退市、ST、
板块和 universe membership 生效日做 PIT 契约测试。缺少这些表时 market run 必须失败，不能退化为
当前股票列表。

### 12.7 SampleProvider 抽象过度

当前只有 market 需求，因此只实现具体 `MarketSampleProvider`。不创建 Protocol registry、kind enum、
event/index/custom/hybrid 空类或 capability discovery；新样本类型只有在真实需求出现后另案设计。

### 12.8 历史行为可能冻结已知错误

只保留 `normative_correctness`，证明新的领域规则正确；不建立旧行为 parity 契约。与历史结果冲突时记录
原因并修复正确规则，禁止为保持旧输出而保留错误。
已知必须覆盖：limit20 不能继续使用默认 10% LimitBook、IC ties/NaN、退市/停牌估值、
日线止盈止损同日路径不确定性。

### 12.9 复权契约不一致

设计示例的 `qfq_pit` 与当前 `zer0factor` 的 `hfq|qfq|none`、默认 `hfq` 不一致。qrsearch adapter 必须
显式映射并校验名称、公式、基准时点和 corporate-action 可见性，以拆分/分红合成样本验证；无法证明
一致时明确失败。不得为了对齐而在当前迭代修改两个上游仓库。

### 12.10 全市场性能不足

当前事件级代码存在逐行 `iter_rows`、反复 `calendar.index()`、整段因子 Pandas 读取和全量
Python dict 索引。通用路径使用 Arrow Dataset/Parquet predicate pushdown、Polars LazyFrame、
日历 ordinal 映射和一次性向量化标签；禁止按 observation 查询因子。验收必须包含峰值内存、
物化耗时、缓存命中耗时和样本规模，而不只是正确性单测。

### 12.11 数据缺失、停牌与退市偏差

缺失未来价格不能静默删样本，回测也不能长期按买入价估值。Feature/Label/Backtest 分别记录
缺失原因、最近估值日和 stale age；超过协议阈值时使用明确 censor/defer/conservative mark
规则。理论收益与可交易收益分开报告。

### 12.12 因子统计显著性被高估

重叠 horizon、重复事件和同日截面使普通 ICIR/t 统计失真。按协议使用 HAC 或 block bootstrap，
事件样本披露日期/股票聚类结果；多重选择使用 FDR、Deflated Sharpe，并记录全部被试因子和
参数。未完成校正时只能作为探索结果。

### 12.13 缓存与 lineage 断裂

缓存状态和数据身份分开保存。每个缓存对象包含内容哈希、源数据指纹、查询条件、schema、
代码版本和上游 manifest；缓存命中仍返回原数据指纹。任何依赖变化都使阶段 cache key 失效。

## 13. 验收标准

设计完成后的系统必须满足：

- CLI 只运行 zer0share market universe + zer0factor 因子这一条路径，所有保留命令均无 `--csv`。
- market 使用逐日 PIT universe，合成退市与调仓案例不出现幸存者偏差。
- `qrsearch` 代码中不存在新的日频因子公式或可写因子存储。
- 每个正式 run 使用固定 FeatureSnapshot，不读取可变 latest 因子。
- 配置只能增加 manifest 延迟；随机抽样逐行核对时，因子 `available_at <= asof_time`。
- split manifest 按 fold 证明训练与 OOS 标签区间没有禁止重叠，并列出 purge/embargo ID。
- Iteration 1 的正确性修复由底层统计/成交合成测试继续覆盖；event contract 随旧路径删除。
- normative tests 覆盖 limit10/limit20、IC ties/NaN、停牌/退市、OHLC 双触发和复权事件；
  与历史行为冲突时以正确规则为准。
- 三个仓库版本、数据指纹、因子 manifest 和试验次数均进入 lineage。
- 因子评估披露 HAC/block-bootstrap、有效样本量和多重试验校正状态。
- 全市场基准达到预先声明的样本规模、峰值内存与耗时预算。
- `config · tests · Skill · README` 四层同时对齐。

## 14. 决策摘要

1. 采用“因子工厂 + 通用研究编排”分层，不做 `qrsearch` 内部大一统数据平台。
2. SampleSet 固定来自 zer0share 逐日 universe；不为尚不存在的其他样本类型建立抽象。
3. `zer0factor` 是唯一因子真源，`qrsearch` 只读版本化 FeatureSnapshot。
4. 评估协议显式区分全市场截面和组合经济表现。
5. 三个迭代顺序开发，Iteration 3 一次性切换；最终不保留兼容适配、双路由或旧版本 fallback。
6. availability 由因子 manifest 掌权，研究配置只能增加延迟。
7. 只冻结规范正确性；golden 不得冻结已知交易或统计错误。
8. market 是唯一当前交付；event/index/custom Provider、全局 FactorArtifact 和更完整 PIT DataContract
   若出现真实需求再分别立项，不为它们预留空实现。
9. 当前三个迭代不修改 zer0share/zer0factor；能力缺口由 qrsearch adapter 处理或另案申请上游变更。
