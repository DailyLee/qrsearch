# Universal Research Kernel Iteration 2: Market Factor Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 `zer0share PIT universe → frozen zer0factor values → zer0factor factor evaluation → qrsearch research dataset` 的唯一研究链路。

**Architecture:** MarketSampleProvider 从 zer0share 的逐日 universe 生成股票×日期观察，qrsearch 冻结 zer0factor 因子值；随后通过 `zer0factor.services.EvaluationService` 完成通用因子诊断。qrsearch 只负责训练样本约束、快照适配、lineage、标签、时间角色和后续 OOS/策略治理，不再实现第二套 IC、分层收益或单调性分析器。

**Tech Stack:** Python 3.11、Pydantic 2、Polars、Pandas、PyArrow/Parquet、Typer、pytest、zer0share LocalPro、zer0factor FactorStorage/EvaluationService。

## Prerequisite

- Iteration 1 completion gate 已通过。
- 本地 zer0share 已生成 `univ_research_base` 或用户配置的逐日 universe。
- 本地 zer0factor FactorStorage 至少存在一个覆盖测试区间的因子。

## Global Constraints

- zer0share、zer0factor 仓库只读；不得修改其代码、配置、数据布局或测试。所有导入、路径、schema、
  指纹、错误翻译和 run-level snapshot 逻辑必须放在 qrsearch。
- 不调用 `zer0share.pro_bar(adj="qfq")`；价格继续使用 qresearch 的 raw OHLC + PIT adj_factor 路径。
- market 观察固定为“当日收盘后可决策”，`effective_session` 固定为下一交易日。
- zer0factor 因子可用延迟必须在配置显式填写；Iteration 2 不猜测字段语义。
- 通用因子分析必须调用 zer0factor EvaluationService；禁止在 qrsearch 重写 IC、分组收益、单调性、换手或
  Alphalens/Pyfolio 指标。qrsearch 只做输入契约与 train-only 审计。
- EvaluationService 必须读取 qrsearch 已冻结的 FeatureSnapshot，并只看到 train membership；不得直接读取
  可变 FactorStorage 或把 validate/holdout 样本交给因子筛选。
- zer0factor `open_t1` forward returns 只用于 train screening；qrsearch LabelSet 是后续 OOS/策略收益的权威口径。
  两者不要求逐行相等，报告必须并列标注来源，禁止把 screening 组合指标当回测结果。
- 只生成理论 forward return，不模拟涨跌停成交。
- 配置不存在 `sample.kind`；出现 `kind`、`sources`、event/CSV 字段时由 `extra="forbid"` 明确拒绝。
- 不实现 FactorArtifact、revision/vintage、HAC、FDR、PBO/CPCV、通用 DAG 或性能优化框架。
- 所有大表写入 `workspace/runs/<run_id>/artifacts/`；stdout 信封只给摘要和路径。
- 不提供旧配置兼容：不增加 mode、deprecated alias、event provider 或 fallback。旧 YAML 解析失败是预期的
  breaking change，用户应从本迭代的新 example 重新生成配置。

## Stable Artifacts

每次 materialize run 必须写：

```text
artifacts/sample_set.parquet
artifacts/feature_snapshot.parquet
artifacts/feature_manifest.json
artifacts/label_set.parquet
artifacts/dataset.parquet
artifacts/split_summary.json
```

evaluate 追加：

```text
artifacts/factor_screening_manifest.json
artifacts/factor_redundancy.parquet
artifacts/zer0factor_evaluation/<screening_run_id>/summary.csv
artifacts/zer0factor_evaluation/<screening_run_id>/summary.parquet
artifacts/zer0factor_evaluation/<screening_run_id>/metadata.json
artifacts/zer0factor_evaluation/<screening_run_id>/report.md
artifacts/zer0factor_evaluation/<screening_run_id>/factors/<factor_name>/daily_ic.parquet
artifacts/zer0factor_evaluation/<screening_run_id>/factors/<factor_name>/quantile_returns.parquet
```

---

### Task 1: Add Minimal Research Configuration and Domain Types

**Files:**
- Create: `qresearch/research/__init__.py`
- Create: `qresearch/research/domain.py`
- Modify: `qresearch/config/models.py`
- Modify: `qresearch/config/__init__.py`
- Create: `tests/test_research_domain.py`

**Interfaces:**

在 `config/models.py` 增加并挂到 `ResearchConfig`。同时删除 `ColumnAliases`、`IngestConfig`、
`FactorPreprocessConfig`、旧 `FactorsConfig` 及 `ResearchConfig.ingest/factors`；保留 `ic_horizons`，仅用于
构造 zer0factor EvaluationRequest。不存在 `ResearchKernelConfig`、sample kind 或 mode 开关：

```python
class SampleConfig(BaseModel):
    universe: str
    start_date: date
    end_date: date

class FeatureRefConfig(BaseModel):
    name: str
    availability_lag_sessions: int = Field(ge=0)

class FeatureSourceConfig(BaseModel):
    provider: Literal["zer0factor"] = "zer0factor"
    refs: list[FeatureRefConfig] = Field(default_factory=list)
    analysis_family: str | None = None

class LabelConfig(BaseModel):
    entry_price: Literal["open", "close"] = "open"
    entry_lag_sessions: int = Field(default=1, ge=1)
    horizon_sessions: int = Field(default=5, ge=1)
    exit_price: Literal["open", "close"] = "open"
```

`domain.py` 定义 frozen dataclass：

```python
@dataclass(frozen=True)
class SampleSet:
    frame: pl.DataFrame
    manifest: dict[str, object]

@dataclass(frozen=True)
class FeatureSnapshot:
    frame: pl.DataFrame
    manifest: dict[str, object]

@dataclass(frozen=True)
class LabelSet:
    frame: pl.DataFrame
    spec: dict[str, object]

@dataclass(frozen=True)
class ResearchDataset:
    frame: pl.DataFrame
    metadata: dict[str, object]
```

同文件还提供两个无状态 helper：

```text
sha256_path(path: Path) -> str
resolve_repo_revision(root: Path) -> tuple[str | None, str | None]
```

`sha256_path` 分块读取文件内容；`resolve_repo_revision` 使用参数数组调用
`git -C <root> rev-parse HEAD`，失败时返回 `(None, error_message)`，禁止使用 shell 字符串拼接。

标准观察键固定为 `sample_id, instrument, asof_session, effective_session`；SampleSet 还必须含
`sample_weight`。manifest 固定记录 `sample_kind="market"`，但不要为单一实现增加 kind 分派接口。
所有日期使用 `pl.Date`，本迭代不引入 datetime/timezone。

- [ ] **Step 1: Write invariant tests**

测试重复观察键、`effective_session <= asof_session`、负权重、缺 universe/起止日期、start>end 和
zer0factor refs 为空。另断言旧 mode、kind、sources、inline provider 和未知字段被拒绝。

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_research_domain.py tests/test_config_new.py -q`
Expected: 新类型不存在导致失败；旧配置拒绝用例通过后才进入实现。

- [ ] **Step 3: Implement exact validators**

规则：universe 非空、start_date<=end_date，并要求 `features.provider=zer0factor` 和非空 refs。
`analysis_family` 为空表示跨家族或无家族分析；非空时必须由 zer0factor EvaluationService 接受，且所有 refs
都必须被该 family 解析，禁止部分跳过后仍返回成功。
模型设置 `extra="forbid"`，旧 mode/kind/sources/inline 字段不得静默忽略。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_research_domain.py tests/test_config_new.py -q`
Expected: 全部通过。

```bash
git add qresearch/research qresearch/config tests/test_research_domain.py
git commit -m "feat: define market research domain"
```

---

### Task 2: Materialize the Point-in-Time Market SampleSet

**Files:**
- Create: `qresearch/research/providers/__init__.py`
- Create: `qresearch/research/providers/market.py`
- Create: `tests/test_market_sample_provider.py`

**Interfaces:**

公开签名固定为：

```text
MarketSampleProvider.__init__(self, pro: object, calendar: list[date]) -> None
MarketSampleProvider.materialize(self, config: SampleConfig) -> SampleSet
```

- [ ] **Step 1: Write provider tests with a fake LocalPro**

Fake `pro.universe()` 返回 `trade_date,universe,ts_code`。用例包含调入、调出和研究期内退市股票；
断言只在返回的逐日 membership 上生成样本，不调用 `stock_basic(list_status="L")`。`sample_id`
固定为 `market:<universe>:<YYYYMMDD>:<ts_code>`。

- [ ] **Step 2: Implement the single provider**

一次调用 `pro.universe(universe=config.universe, start_date=YYYYMMDD(config.start_date),
end_date=YYYYMMDD(config.end_date), fields="trade_date,universe,ts_code")`。
使用交易日历把 asof 后移一 session 得到 effective；末日无下一 session 的行删除并在 manifest 记录
`dropped_no_effective_session`。重复键直接抛 `ResearchDataError`。

- [ ] **Step 3: Persist source identity**

manifest 必须包含 `sample_kind=market`、universe、start/end、rows、instruments、删除计数和 zer0share
数据指纹。不得写 `latest` 或 `cache_hit`。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_market_sample_provider.py -q`
Expected: membership、生效日、重复键和末日删除用例全部通过。

```bash
git add qresearch/research/providers tests/test_market_sample_provider.py
git commit -m "feat: materialize daily market samples"
```

---

### Task 3: Read zer0factor and Freeze a Run-Level FeatureSnapshot

**Files:**
- Modify: `qresearch/config/models.py`
- Modify: `.env.example`
- Create: `qresearch/research/providers/zer0factor.py`
- Create: `tests/test_zer0factor_feature_provider.py`

**Interfaces:**

为 `AppSettings` 增加：

```python
zer0factor_root: str = r"C:\Users\dl271\Downloads\code\zer0factor"
zer0factor_factor_dir: Path = Path("../zer0factor/data/factors")
zer0factor_db_path: Path = Path("../zer0factor/data/factors.duckdb")
```

Provider：

公开签名固定为：

```text
Zer0FactorFeatureProvider.__init__(self, storage: object, calendar: list[date]) -> None
Zer0FactorFeatureProvider.materialize(self, samples: SampleSet, refs: list[FeatureRefConfig]) -> FeatureSnapshot
get_factor_storage(settings: AppSettings) -> object
list_available_factors(storage: object) -> list[str]
```

- [ ] **Step 1: Write lag and coverage tests**

构造因子 `trade_date=20240102`。lag=0 连接到 20240102 asof；lag=1 只连接到下一交易日 asof。
连接必须是 `available_session == asof_session`，不得 forward-fill stale 因子。测试同时覆盖未知因子、
重复 `trade_date/ts_code`、部分缺失和两个 refs 列名冲突。
另断言 `list_available_factors()` 只调用一次 `FactorStorage.list_factors()`，排序后返回非空名称；不读因子值。

- [ ] **Step 2: Implement read-only batch access**

每个 ref 只调用一次 `FactorStorage.read(name, start_date, end_date)`；读取范围向前扩展最大 lag 所需
交易日。将 `ts_code` 改为 `instrument`，value 列改为 `features.<name>`，计算 available_session 后
与 SampleSet 连接。不得调用 storage.write/register/write_partitions。

`get_factor_storage()` 按 zer0share vendor 的导入模式把 `settings.zer0factor_root` 加入 sys.path，随后
构造 `FactorStorage(settings.zer0factor_factor_dir, settings.zer0factor_db_path, init_db=False)`；目录或
DuckDB 不存在时抛 dependency error，不创建替代数据。

- [ ] **Step 3: Build the snapshot manifest and hash**

manifest 对每个因子记录 name、declared lag、rows、coverage、min/max factor date、因子目录指纹；
整体记录 zer0factor repo revision（无法解析 git 时写 package version 和 warning）。
FeatureSnapshot 落盘后使用 SHA256 计算 `feature_snapshot_hash`，该 hash 写入 manifest 和 meta。

- [ ] **Step 4: Verify read-only behavior and commit**

Run: `python -m pytest tests/test_zer0factor_feature_provider.py -q`  
Expected: lag、缺失、重复键、批量调用和禁止写入用例全部通过。

```bash
git add qresearch/research/providers/zer0factor.py qresearch/config/models.py .env.example tests/test_zer0factor_feature_provider.py
git commit -m "feat: freeze zer0factor values into run snapshots"
```

---

### Task 4: Build Fixed-Horizon Labels and a Temporal Dataset

**Files:**
- Create: `qresearch/research/labels.py`
- Create: `qresearch/research/dataset.py`
- Create: `tests/test_market_labels.py`
- Create: `tests/test_research_dataset.py`

**Interfaces:**

```text
load_research_price_panel(samples: SampleSet, label: LabelConfig, research: ResearchConfig, cache_dir: Path) -> PricePanel
materialize_labels(samples: SampleSet, panel: PricePanel, config: LabelConfig) -> LabelSet
build_research_dataset(samples: SampleSet, features: FeatureSnapshot, labels: LabelSet) -> ResearchDataset
```

LabelSet 每行固定包含观察键、`label_start`、`label_end`、`forward_return`、`label_status`。
`label_status` 仅允许 `ok|missing_entry|missing_exit|no_calendar_session`。

- [ ] **Step 1: Write price and missing-label tests**

覆盖 open T+1 → open T+6、close T+1 → close T+6、拆分期间 PIT qfq、缺 entry、缺 exit 和日历越界。
缺失标签保留行并写 status，`forward_return=null`；不得删掉失败股票。

- [ ] **Step 2: Implement label materialization**

entry session 从 asof 按 `entry_lag_sessions` 移动，exit 再移动 `horizon_sessions`。两端价格统一以
exit session 为 qfq as-of，分别调用 `panel.get(instrument, entry_session, asof=exit_session)` 和
`panel.get(instrument, exit_session, asof=exit_session)`。本迭代不检查涨跌停成交。

`load_research_price_panel()` 先把 samples 投影成临时 DataFrame：instrument、
`entry_intent_date=effective_session`、`exit_intent_date=effective_session` 后移 horizon；再复用现有
`load_price_panel()`。该兼容底层引擎 schema 的临时表只在内存中使用，不写 CSV；Iteration 3 删除
对外 event/CSV 概念后，可保留这些内部列名直到单独重构有收益。

- [ ] **Step 3: Implement one-to-one dataset assembly**

按完整观察键 left join。输出不得改变 SampleSet 行数；features 或 labels 出现额外键、重复键时抛
`ResearchDataError`。metadata 记录 feature coverage、label status 计数和 input hashes。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_market_labels.py tests/test_research_dataset.py tests/test_pit_qfq.py -q`  
Expected: 全部通过。

```bash
git add qresearch/research/labels.py qresearch/research/dataset.py tests/test_market_labels.py tests/test_research_dataset.py
git commit -m "feat: materialize market labels and datasets"
```

---

### Task 5: Run Train-Only Factor Analysis through zer0factor

**Files:**
- Create: `qresearch/research/splits.py`
- Create: `qresearch/research/providers/zer0factor_evaluation.py`
- Create: `qresearch/research/redundancy.py`
- Create: `tests/test_research_splits.py`
- Create: `tests/test_zer0factor_evaluation_provider.py`
- Create: `tests/test_factor_redundancy.py`

**Interfaces:**

```text
assign_temporal_roles(dataset: ResearchDataset, evaluation: EvaluationConfig) -> ResearchDataset
FrozenSnapshotStorage.__init__(snapshot: FeatureSnapshot) -> None
FrozenSnapshotStorage.read(factor_name: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame
TrainUniversePro.__init__(pro: object, train_samples: pl.DataFrame) -> None
TrainUniversePro.universe(universe: str, start_date: str, end_date: str | None, fields: str) -> pd.DataFrame
TrainUniversePro.pro_bar(**kwargs: object) -> pd.DataFrame
TrainUniversePro.index_daily(**kwargs: object) -> pd.DataFrame
run_factor_screening(dataset: ResearchDataset, snapshot: FeatureSnapshot, config: ResearchConfig, pro: object, output_dir: Path, run_id: str) -> FactorScreeningResult
compute_train_factor_redundancy(dataset: ResearchDataset, feature_names: list[str]) -> pl.DataFrame
```

在 `domain.py` 增加：

```python
@dataclass(frozen=True)
class FactorScreeningResult:
    summary: pl.DataFrame
    run_dir: Path
    manifest: dict[str, object]
```

- [ ] **Step 1: Write temporal-role and purge tests**

使用 `evaluation.train_years/validate_years/holdouts`。角色只能是 train、validate、holdout_final、
holdout_stress。删除 train 中 `label_end >= first_non_train_asof` 的重叠标签，并记录
`purged_train_count`。构造不连续 train 年和同日期多股票样本，证明角色由明确年份决定，不按 DataFrame
位置或简单 start/end 猜测；holdout 不能出现在 screening membership。

- [ ] **Step 2: Write frozen-storage adapter tests**

`FrozenSnapshotStorage.read()` 只从 `feature_snapshot.parquet` 对应的内存快照读取，将
`instrument/asof_session/features.<name>` 映射为 zer0factor 所需的 `ts_code/trade_date/value`。按请求日期过滤，
未知因子、重复键或 snapshot manifest hash 不一致直接抛 `ResearchDataError`。测试 monkeypatch 原始
FactorStorage.read 为抛异常，证明 screening 不会回读可变存储。

- [ ] **Step 3: Write train-universe adapter tests**

`TrainUniversePro.universe()` 忽略上游返回的额外 membership，只返回 dataset 中 `role=train` 的
`trade_date, universe, ts_code`；validate/holdout 股票日期即使位于 start/end 范围内也不得出现。
`pro_bar()` 和 `index_daily()` 仅透传到 qrsearch 已配置的 zer0share LocalPro。禁止使用
`stock_basic(list_status="L")` 或当前上市列表构造历史样本。

- [ ] **Step 4: Invoke the public zer0factor service**

在 Task 3 已验证的 `settings.zer0factor_root` 导入边界内，只导入公开类型：
`from zer0factor.services.evaluate import EvaluationService` 和
`from zer0factor.eval.domain import EvaluationRequest`。导入失败映射为 exit 5 dependency error。

使用 `EvaluationService.from_dependencies(storage=FrozenSnapshotStorage(snapshot),
pro=TrainUniversePro(pro, train_samples))`，再构造 `EvaluationRequest`：

```python
request = EvaluationRequest(
    factor_names=tuple(ref.name for ref in config.features.refs),
    factor_source="explicit",
    start_date=min_train_session.strftime("%Y%m%d"),
    end_date=max_train_session.strftime("%Y%m%d"),
    periods=tuple(config.ic_horizons),
    return_type="open_t1",
    universe=config.sample.universe,
    output_dir=output_dir / "zer0factor_evaluation",
    benchmark_index=config.benchmark.instrument,
    workers=1,
    generate_report=True,
)
```

调用 `service.run(request, run_id=f"{run_id}_train")`。Iteration 2 固定 `workers=1`，避免自定义 adapter 被进程池复制；
不导入 zer0factor 的私有 evaluator/metrics 函数，也不复制其计算公式到 qrsearch。

- [ ] **Step 5: Add the one qrsearch-specific supplemental diagnostic**

zer0factor 当前 EvaluationService 不输出跨因子冗余矩阵。`compute_train_factor_redundancy()` 只在 role=train
行内，按同一 `asof_session` 做截面 rank 后计算因子两两 Spearman，输出
`factor_a,factor_b,mean_daily_rank_corr,valid_dates`。它不计算因子收益、IC、分层、方向或综合评分，也不
自动删除因子；Agent 结合经济含义和 zer0factor 证据判断冗余。validate/holdout 行混入时测试必须失败。

- [ ] **Step 6: Validate and record upstream artifacts**

要求 summary、metadata、report、每个因子的 daily_ic 和 quantile_returns 均存在；缺任一项 exit 3。逐行检查
`clean_factor_data` 的日期×股票属于 train membership，并核对 factor_names、periods、return_type、universe、
FeatureSnapshot SHA256 和 zer0factor revision。写 `factor_screening_manifest.json` 和
`factor_redundancy.parquet`，记录 zer0factor run_dir、
请求参数、输入 hash、artifact hash 和被排除的 validate/holdout 行数。zer0factor 输出是 train screening
证据，不得标记为 OOS 或 promotable。

- [ ] **Step 7: Verify and commit**

Run: `python -m pytest tests/test_research_splits.py tests/test_zer0factor_evaluation_provider.py tests/test_factor_redundancy.py -q`
Expected: train-only membership、冻结存储、EvaluationService 公共接口映射、artifact 审计和禁止回读全部通过。

```bash
git add qresearch/research/domain.py qresearch/research/splits.py qresearch/research/redundancy.py qresearch/research/providers/zer0factor_evaluation.py tests/test_research_splits.py tests/test_zer0factor_evaluation_provider.py tests/test_factor_redundancy.py
git commit -m "feat: delegate factor screening to zer0factor"
```

---

### Task 6: Expose Market Research CLI and Retire the CSV/Event Surface

**Files:**
- Create: `qresearch/research/pipeline.py`
- Modify: `qresearch/cli.py`
- Modify: `qresearch/engines/data/__init__.py`
- Delete: `qresearch/engines/data/ingest.py`
- Delete: `qresearch/engines/ops/__init__.py`
- Delete: `qresearch/engines/ops/runner.py`
- Delete: `qresearch/engines/factor/__init__.py`
- Delete: `qresearch/engines/factor/band_ic.py`
- Delete: `qresearch/engines/factor/diagnostics.py`
- Delete: `qresearch/engines/factor/ic.py`
- Delete: `qresearch/engines/factor/preprocess.py`
- Delete: `qresearch/engines/factor/sample_profile.py`
- Delete: `qresearch/engines/factor/universe.py`
- Create: `tests/test_market_research_cli.py`
- Create: `tests/test_no_csv_surface.py`
- Delete: `tests/test_ingest_helpers.py`
- Delete: `tests/test_wf_and_ingest.py`
- Delete: `tests/test_event_research_contract.py`
- Delete: `tests/test_factor_compare_persist.py`
- Delete: `tests/test_band_ic_and_between_sweep.py`
- Delete: `tests/test_factor_diagnostics.py`
- Delete: `tests/test_factor_hardening.py`
- Delete: `tests/test_factor_ic.py`
- Delete: `tests/test_factor_preprocess.py`
- Delete: `tests/test_ops_runner.py`
- Modify: `tests/test_evaluation_protocol.py`
- Modify: `tests/test_p0.py`
- Modify: `tests/test_pit_qfq.py`
- Modify: `tests/test_protect_events_hook.py`
- Delete: `configs/examples/event_factors.yaml`
- Create: `configs/examples/market_factors.yaml`
- Modify: `README.md`
- Modify: `.agents/skills/qresearch/reference.md`
- Modify: `.agents/skills/qresearch/factor-analysis.md`

**Interfaces:**

```text
materialize_research(config_path: str | Path, run_id: str | None = None) -> dict[str, object]
evaluate_research(config_path: str | Path, run_id: str | None = None) -> dict[str, object]
```

CLI 增加 Typer group `research`：

```text
qr research factors --format json --quiet
qr research materialize --config <yaml> --format json --quiet
qr research evaluate --config <yaml> --format json --quiet
```

`research factors` 调用 Task 3 的 `list_available_factors()`，只返回 zer0factor FactorStorage 中可读取的名称，
不扫描公式、不计算因子、不创建 run。

- [ ] **Step 1: Write CLI envelope tests**

先测试 `research factors` 返回排序名称和单 JSON。再用 fake providers 运行 materialize/evaluate，断言
stdout 只有一个 JSON；包含 schema_version、run_id、sample rows、feature
coverage、label status、snapshot hash、zer0factor screening run_id/summary/report 和全部 artifact 路径。
配置错误 exit 2，数据/覆盖/zer0factor artifact 审计错误 exit 3，
依赖缺失 exit 5。

- [ ] **Step 2: Implement the linear pipeline**

`materialize` 顺序固定为 samples → features → price panel → labels → dataset → roles。
`evaluate` 先复用同一 run 的冻结 dataset/snapshot，再调用 Task 5 的 `run_factor_screening()`；不得调用
qrsearch 旧 `compute_ic_table/spearman_ic/quantile` 组成第二套分析，也不得引入 ResearchStage、DAG registry
或通用缓存。

Agent 使用路径必须写入 `.agents/skills/qresearch/factor-analysis.md` 和 `reference.md`：

```text
qr data ping
  → qr research factors
  → qr config new（market 模板，填写候选 zer0factor refs、universe、时间角色）
  → qr research materialize
  → qr research evaluate
  → 读取 zer0factor summary/report/daily_ic/quantile_returns
  → 读取 qrsearch factor_redundancy.parquet
  → 只基于 train 证据写 factor_analysis decision
  → 进入 Iteration 3 的策略设计、OOS 回测和门禁
```

Skill 必须明确：`research evaluate` 内部调用 zer0factor EvaluationService；Agent 不再调用已删除的
`qr factor compare/preprocess/band-ic`，也不得从 validate/holdout 结果反向选择因子。raw 与中性化对照通过
同时列出 zer0factor 中已经物化的对应 factor refs 完成，qrsearch 不现场做预处理。

- [ ] **Step 3: Add one example skeleton**

新增唯一的 market example；`features.refs` 保持空列表，不写可复制的假因子或策略信号。不要保留
event/CSV 字段注释或迁移别名。真实实验必须由 `qr config new` 写入 `configs/experiments/`。

- [ ] **Step 4: Delete the old CSV/event surface**

删除 `data validate-events`、旧 `pipeline research/optimize/sweep/sensitivity`、`factor ic/preprocess/compare/band-ic`、
`backtest run`、`validate rolling` 和 `ops run`。删除 `load_events/validate_events/resolve_event_paths`、event
ingest aliases、ops runner、qrsearch 的旧 factor IC/quantile/preprocess/diagnostics 实现、event example 及只服务
这些入口的测试。仍有价值的 PIT 复权和成交断言迁移到 market label/strategy 合成 fixture；Spearman、分层、
单调性和预处理由 zer0factor 自己的测试负责，不在 qrsearch 复制。不得为了让测试通过而保留旧分析器或
event loader。不得修改或删除历史 `workspace/events/**`、
`workspace/events_ascii/**` 数据。

`tests/test_no_csv_surface.py` 使用 Typer CliRunner 断言所有保留命令的 help 均不含 `--csv`，并扫描
`qresearch/**` 不含 `load_events`、`validate_events`、`events_path`、`EventSampleProvider`、
`compute_ic_table`、`compute_quantile_returns` 或 `apply_factor_preprocess`。Iteration 2 结束时
策略/优化入口暂不可用是明确边界；Iteration 3 基于冻结 market dataset 恢复这些能力。

- [ ] **Step 5: Run iteration verification**

Run: `python -m pytest tests/test_market_research_cli.py tests/test_no_csv_surface.py tests/test_market_sample_provider.py tests/test_zer0factor_feature_provider.py tests/test_market_labels.py tests/test_research_dataset.py tests/test_research_splits.py tests/test_zer0factor_evaluation_provider.py tests/test_factor_redundancy.py -q`
Expected: 全部通过。  
Run: `python -m pytest -q --ignore=tests/test_protect_events_hook.py`  
Expected: 全部通过。  
Run: `python -m qresearch research materialize --config <local_market_config> --format json --quiet`  
Expected: 本地数据齐全时成功；缺数据时 exit 3/5 且错误指出缺少的 universe 或 factor，不允许 skip 成功。

- [ ] **Step 6: Commit**

```bash
git add -A qresearch tests configs/examples README.md .agents/skills/qresearch
git commit -m "feat: expose market factor research workflow"
```

## Iteration 2 Completion Gate

- 只有 zer0share market universe SampleSet；不存在 event/CSV SampleProvider、kind 分派或 inline factor 路径。
- Iteration 1 的底层正确性用例继续通过；event contract 与旧路径已删除。
- market SampleSet 来自逐日 zer0share universe，不使用当前上市列表。
- zer0factor 只读且每个 run 固定 FeatureSnapshot SHA256。
- labels 保留缺失状态；IC、分层收益、单调性、换手和基础组合指标由 zer0factor 生成。
- zer0factor screening 只看到 train membership，并读取 qrsearch 冻结的 FeatureSnapshot；qrsearch 没有第二套
  通用因子分析实现。
- CLI 只提供 market materialize/evaluate；策略、优化和回测入口由 Iteration 3 基于同一 dataset 恢复。
- 没有 index/custom、FactorArtifact、DAG、HAC/FDR/PBO 的空实现。

满足全部条件后才开始 Iteration 3。
