# Universal Research Kernel Iteration 2: Event and Market Factor Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 `event/market samples → zer0factor values → fixed run snapshot → forward labels → date-wise IC` 的单内核最小研究链路。

**Architecture:** 新链路放在 `qresearch/research/`，event 与 market 只在 SampleProvider 不同，之后共享 zer0factor、标签、dataset 和评估流程。采用普通线性函数编排，不建立通用 DAG；因子在 run 内复制并哈希，不改造 zer0factor 全局存储。

**Tech Stack:** Python 3.11、Pydantic 2、Polars、PyArrow/Parquet、NumPy、Typer、pytest、zer0share LocalPro、zer0factor FactorStorage。

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
- 只生成理论 forward return，不模拟涨跌停成交。
- 未支持的 `sample.kind=index|custom|hybrid` 在配置校验阶段明确拒绝，不返回空数据。
- 不实现 FactorArtifact、revision/vintage、HAC、FDR、PBO/CPCV、通用 DAG 或性能优化框架。
- 所有大表写入 `workspace/runs/<run_id>/artifacts/`；stdout 信封只给摘要和路径。
- 不提供旧配置兼容：不增加 mode、deprecated alias、legacy provider 或 fallback。旧 YAML 解析失败是预期的
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
artifacts/ic_daily.parquet
artifacts/evaluation_summary.json
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

在 `config/models.py` 增加并挂到 `ResearchConfig`。不存在 `ResearchKernelConfig` 或 mode 开关：

```python
class SampleConfig(BaseModel):
    kind: Literal["event", "market"] = "event"
    sources: list[str] = Field(default_factory=list)
    universe: str | None = None
    start_date: date | None = None
    end_date: date | None = None

class FeatureRefConfig(BaseModel):
    name: str
    availability_lag_sessions: int = Field(ge=0)

class FeatureSourceConfig(BaseModel):
    provider: Literal["zer0factor"] = "zer0factor"
    refs: list[FeatureRefConfig] = Field(default_factory=list)

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
    kind: Literal["event", "market"]
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
`sample_weight`；event SampleSet 可额外包含 nullable `event_exit_session`。所有日期使用 `pl.Date`，
本迭代不引入 datetime/timezone。

- [ ] **Step 1: Write invariant tests**

测试重复观察键、`effective_session <= asof_session`、负权重、event 缺 sources、market 缺 universe/起止
日期、两类字段混用和 zer0factor refs 为空。另断言旧 mode、legacy provider 和未知字段被拒绝。

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_research_domain.py tests/test_config_new.py -q`  
Expected: 新类型不存在导致失败；旧配置拒绝用例通过后才进入实现。

- [ ] **Step 3: Implement exact validators**

规则：event 要求非空 sources 且 universe/start/end 为空；market 要求 sources 为空且
universe/start/end 非空；两类都要求 `features.provider=zer0factor` 和非空 refs。模型设置
`extra="forbid"`，旧 mode/legacy 字段不得静默忽略。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_research_domain.py tests/test_config_new.py tests/test_ingest_helpers.py -q`  
Expected: 全部通过。

```bash
git add qresearch/research qresearch/config tests/test_research_domain.py
git commit -m "feat: define unified event and market research domain"
```

---

### Task 2: Materialize Point-in-Time Event and Market SampleSets

**Files:**
- Create: `qresearch/research/providers/__init__.py`
- Create: `qresearch/research/providers/event.py`
- Create: `qresearch/research/providers/market.py`
- Create: `tests/test_event_sample_provider.py`
- Create: `tests/test_market_sample_provider.py`

**Interfaces:**

公开签名固定为：

```text
EventSampleProvider.__init__(self, calendar: list[date]) -> None
EventSampleProvider.materialize(self, config: SampleConfig, research: ResearchConfig) -> SampleSet
MarketSampleProvider.__init__(self, pro: object, calendar: list[date]) -> None
MarketSampleProvider.materialize(self, config: SampleConfig) -> SampleSet
```

- [ ] **Step 1: Write event-provider tests**

复用现有 `load_events(paths, config)` 只负责 CSV alias、日期和 board 校验，再投影成标准观察键。
`asof_session=decision_date`、`effective_session=entry_intent_date`、`event_exit_session=exit_intent_date`；
`sample_id` 固定为 `event:<source_sha256>:<source_line_number>`；line number 在读取原 CSV 时生成并贯穿
标准化流程，不能在过滤/拼接后重新编号。这样相同业务键的多次事件仍可区分，完全重复 sample_id 才报错。
测试明确断言输入 CSV 中任何 `features.*` 列均被丢弃，因子只能在 Task 3 从 zer0factor join，防止事件文件
成为第二因子真源。

- [ ] **Step 2: Write market-provider tests with a fake LocalPro**

Fake `pro.universe()` 返回 `trade_date,universe,ts_code`。用例包含调入、调出和研究期内退市股票；
断言只在返回的逐日 membership 上生成样本，不调用 `stock_basic(list_status="L")`。`sample_id`
固定为 `market:<universe>:<YYYYMMDD>:<ts_code>`。

- [ ] **Step 3: Implement both providers**

Event provider 保留合法的 nullable `event_exit_session`，供 Iteration 3 的事件退出策略选择使用；研究标签仍
使用统一 LabelConfig，不把事件退出日偷偷当作 forward label horizon。

一次调用 `pro.universe(universe=config.universe, start_date=YYYYMMDD(config.start_date),
end_date=YYYYMMDD(config.end_date), fields="trade_date,universe,ts_code")`。
使用交易日历把 asof 后移一 session 得到 effective；末日无下一 session 的行删除并在 manifest 记录
`dropped_no_effective_session`。重复键直接抛 `ResearchDataError`。

- [ ] **Step 4: Persist source identity**

manifest 必须包含 kind、source identity、rows、instruments 和删除计数。market 还包含 universe、
start/end 与 zer0share 数据指纹；event 包含每个只读 CSV 的内容哈希。不得写 `latest` 或 `cache_hit`。

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/test_event_sample_provider.py tests/test_market_sample_provider.py -q`  
Expected: event 映射/忽略 inline features、market membership、生效日、重复键和末日删除用例全部通过。

```bash
git add qresearch/research/providers tests/test_event_sample_provider.py tests/test_market_sample_provider.py
git commit -m "feat: materialize event and daily market samples"
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
```

- [ ] **Step 1: Write lag and coverage tests**

构造因子 `trade_date=20240102`。lag=0 连接到 20240102 asof；lag=1 只连接到下一交易日 asof。
连接必须是 `available_session == asof_session`，不得 forward-fill stale 因子。测试同时覆盖未知因子、
重复 `trade_date/ts_code`、部分缺失和两个 refs 列名冲突。

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
`load_price_panel()`。该临时表只在内存中使用，不写成事件 CSV。

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

### Task 5: Add Temporal Roles and Date-Wise Factor Evaluation

**Files:**
- Create: `qresearch/research/evaluation.py`
- Create: `tests/test_market_evaluation.py`

**Interfaces:**

```text
assign_temporal_roles(dataset: ResearchDataset, evaluation: EvaluationConfig) -> ResearchDataset
evaluate_factors(dataset: ResearchDataset, feature_names: list[str]) -> dict[str, object]
```

- [ ] **Step 1: Write split tests**

使用现有 `evaluation.train_years/validate_years/holdouts`。角色只能是 train、validate、holdout_final、
holdout_stress。删除 train 中 `label_end >= first_non_train_asof` 的重叠标签，并在 metadata 记录
`purged_train_count`；holdout 不得参与任何因子方向选择。

- [ ] **Step 2: Write date-wise IC tests**

每个 asof_session 内计算 Spearman，再聚合 mean/std/ICIR、direction win rate、有效日期数和总有效
样本数。构造数据证明 pooled Spearman 与 date-wise mean 不同；primary 输出只能使用 date-wise。

- [ ] **Step 3: Implement the evaluator**

调用 Iteration 1 修正后的 `spearman_ic()`。每日有效样本少于 3 时跳过该日并计数。只在 train 输出
候选方向；validate/holdout 只按已冻结方向披露。本迭代不实现 HAC/FDR/PBO。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_market_evaluation.py tests/test_factor_ic.py -q`  
Expected: role、purge、date-wise IC 和 pooled diagnostic 用例全部通过。

```bash
git add qresearch/research/evaluation.py tests/test_market_evaluation.py
git commit -m "feat: evaluate factors on temporal market samples"
```

---

### Task 6: Expose Materialize and Evaluate CLI Commands

**Files:**
- Create: `qresearch/research/pipeline.py`
- Modify: `qresearch/cli.py`
- Create: `tests/test_market_research_cli.py`
- Modify: `configs/examples/event_factors.yaml`
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
qr research materialize --config <yaml> --format json --quiet
qr research evaluate --config <yaml> --format json --quiet
```

- [ ] **Step 1: Write CLI envelope tests**

用 fake providers 运行，断言 stdout 只有一个 JSON；包含 schema_version、run_id、sample rows、feature
coverage、label status、snapshot hash 和全部 artifact 路径。配置错误 exit 2，数据/覆盖错误 exit 3，
依赖缺失 exit 5。

- [ ] **Step 2: Implement the linear pipeline**

顺序固定为 samples → features → price panel → labels → dataset → roles → evaluation。每一步立即写
稳定 artifact；不得引入 ResearchStage、DAG registry 或通用缓存。

- [ ] **Step 3: Add one example skeleton**

把现有 event example 一次性改成新 schema，并增加 market example；两者 `features.refs` 保持空列表，
不写可复制的假因子或策略信号。不要保留旧字段注释或迁移别名。真实实验必须由 `qr config new` 写入
`configs/experiments/`。

- [ ] **Step 4: Run iteration verification**

Run: `python -m pytest tests/test_market_research_cli.py tests/test_event_sample_provider.py tests/test_market_sample_provider.py tests/test_zer0factor_feature_provider.py tests/test_market_labels.py tests/test_research_dataset.py tests/test_market_evaluation.py -q`  
Expected: 全部通过。  
Run: `python -m pytest -q --ignore=tests/test_protect_events_hook.py`  
Expected: 全部通过。  
Run: `python -m qresearch research materialize --config <local_market_config> --format json --quiet`  
Expected: 本地数据齐全时成功；缺数据时 exit 3/5 且错误指出缺少的 universe 或 factor，不允许 skip 成功。

- [ ] **Step 5: Commit**

```bash
git add qresearch/research qresearch/cli.py qresearch/config tests configs/examples/event_factors.yaml README.md .agents/skills/qresearch
git commit -m "feat: expose market factor research workflow"
```

## Iteration 2 Completion Gate

- event 与 market 均由新 SampleProvider 进入同一研究内核；不存在 legacy mode 或 inline factor 路径。
- Iteration 1 的规范业务 contract 继续通过，但不要求旧 CLI/YAML/API 存活。
- market SampleSet 来自逐日 zer0share universe，不使用当前上市列表。
- zer0factor 只读且每个 run 固定 FeatureSnapshot SHA256。
- labels 保留缺失状态，date-wise IC 是 primary。
- CLI 只提供 materialize/evaluate；`pipeline research` 的一次性替换留给 Iteration 3。
- 没有 index/custom、FactorArtifact、DAG、HAC/FDR/PBO 的空实现。

满足全部条件后才开始 Iteration 3。
