# Universal Research Kernel Iteration 3: Strategy, Backtest, and CLI Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Iteration 2 的 event/market ResearchDataset 接入唯一一套信号、日频回测、报告和 promote 门禁，并一次性替换旧事件编排。

**Architecture:** 不重写 signal/backtest；新增统一 strategy adapter，把 event/market 观察转换为现有 `build_ranked()` 和 `run_backtest()` 所需列。`pipeline research` 只有一条新内核路径；切换完成后删除旧编排、旧参数和兼容代码。

**Tech Stack:** Python 3.11、Pydantic 2、Polars、Typer、Jinja2、pytest、Iteration 1/2 产物。

## Prerequisite

- Iteration 1 和 Iteration 2 completion gate 全部通过。
- `qr research materialize/evaluate` 已能生成固定 snapshot、dataset 和 date-wise IC。

## Global Constraints

- `sample.kind=event|market` 均使用新内核；event CSV 路径只允许写在 `sample.sources`，CLI 不再接受 `--csv`。
- 不保留 kernel mode、旧 YAML adapter、deprecated option、双路径 dispatcher 或 fallback；旧调用应明确失败。
- zer0share、zer0factor 仓库只读；本迭代所有实现和适配只能落在 qrsearch。
- signal/backtest 不重新查询 zer0factor，只读取 run 内 `dataset.parquet`。
- 策略退出必须显式选择 `strategy.holding.kind=fixed_sessions|event_exit`；后者仅允许 event sample。
- 成交使用 Iteration 1 的历史 `up_limit/down_limit` 和停牌规则。
- `best_value` 不等于 promotable；继续执行现有 quality gates、sensitivity 和 holdout 纪律。
- 不实现配置迁移、index/custom、FactorArtifact、DAG、HAC/FDR/PBO。
- 不创建空 Provider 或返回空成功信封；不支持即 exit 2。

---

### Task 1: Adapt Event and Market Observations to Existing Signal Inputs

**Files:**
- Create: `qresearch/research/strategy.py`
- Create: `tests/test_strategy_adapter.py`
- Modify: `qresearch/config/models.py`

**Interfaces:**

```text
build_strategy_event_frame(dataset: ResearchDataset, config: ResearchConfig, calendar: list[date]) -> pl.DataFrame
```

新增配置模型并挂到策略配置：

```python
class HoldingConfig(BaseModel):
    kind: Literal["fixed_sessions", "event_exit"] = "fixed_sessions"
    sessions: int | None = Field(default=None, ge=1)
```

`fixed_sessions` 必须提供 sessions；`event_exit` 禁止提供 sessions，且只允许 event SampleSet。
输出列固定为：`instrument, decision_date, entry_intent_date, exit_intent_date` 加全部 `features.*`。
`decision_date=asof_session`、`entry_intent_date=effective_session`；fixed_sessions 按日历后移，event_exit
读取 SampleSet 的 `event_exit_session`。不允许猜测默认持有期。

- [ ] **Step 1: Write mapping tests**

覆盖两种 sample kind、两种 holding kind、feature 列保留、同一股票不同日观察、末端无退出 session、
非法组合和重复观察键。末端无退出 session 的行删除并返回计数；配置缺 sessions 或 event 缺退出日时失败。

- [ ] **Step 2: Add exact holding validation**

固定持有要求 `risk.exit_priority` 包含 `max_hold`；事件退出要求包含 `exit_intent`。配置模型用
`extra="forbid"` 拒绝旧 `risk.max_hold_sessions` 和 mode 字段，不提供 alias。

- [ ] **Step 3: Implement without signal duplication**

adapter 只做列映射和日期移动，随后直接调用现有 `build_ranked(frame, config)`；不得复制 filters、
rank、composite 实现。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_strategy_adapter.py tests/test_signal_engine.py -q`  
Expected: 全部通过。

```bash
git add qresearch/research/strategy.py qresearch/config/models.py tests/test_strategy_adapter.py
git commit -m "feat: adapt unified research datasets to signals"
```

---

### Task 2: Run the Existing Backtest from a Frozen Event or Market Dataset

**Files:**
- Modify: `qresearch/research/pipeline.py`
- Create: `tests/test_research_backtest_pipeline.py`

**Interfaces:**

```text
run_research_strategy(config_path: str | Path, run_id: str | None = None, n_trials_assumed: int | None = None) -> dict[str, object]
```

- [ ] **Step 1: Write the no-requery contract**

先 materialize dataset，再 monkeypatch Zer0FactorFeatureProvider 使任何后续调用都抛异常；
`run_research_strategy()` 必须仍能从 `artifacts/dataset.parquet` 完成信号和回测。event 与 market 各跑
一个合成用例，并断言写出
`ranked_events.parquet, equity.csv, trades.csv, metrics.json, rejects_summary.json`。

- [ ] **Step 2: Reuse the existing backtest path**

`run_research_strategy()` 先以同一 run_id 调用 `materialize_research()`，随后从该 run 的
`artifacts/dataset.parquet` 重新读取冻结数据；再使用 `build_strategy_event_frame()` → `build_ranked()` →
`load_price_panel()` → `run_backtest()`。
metrics 继续调用 `attach_overfit_metrics()` 和 `mean_invested_from_equity()`；不得建立第二套成本、
组合、T+1、涨跌停或退出逻辑。

- [ ] **Step 3: Attach unified provenance**

meta 至少写 sample kind、sample identity（event sources hash 或 market universe）、feature snapshot hash、label spec、split summary、
zer0share fingerprint、zer0factor fingerprint/revision、n_trials_assumed 和
`execution_model=daily_open_historical_limits`。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_research_backtest_pipeline.py tests/test_session_contracts.py tests/test_evaluation_protocol.py -q`  
Expected: 全部通过。

```bash
git add qresearch/research/pipeline.py tests/test_research_backtest_pipeline.py
git commit -m "feat: backtest frozen research datasets"
```

---

### Task 3: Replace `pipeline research` with the Single Kernel

**Files:**
- Modify: `qresearch/cli.py`
- Modify: `qresearch/pipeline.py`
- Create: `tests/test_research_pipeline_cli.py`

**Interfaces:**

唯一公开入口：

```text
pipeline_research(config_path: str | Path, run_id: str | None, n_trials_assumed: int | None) -> dict[str, object]
qr pipeline research --config <yaml> [--run-id <id>] [--n-trials-assumed <n>]
```

- [ ] **Step 1: Write the breaking CLI contract first**

测试 event 与 market 配置都调用同一个 `run_research_strategy()`。`--config` 必填；删除 `--csv` option，
传入 `--csv` 应由 Typer 返回配置/用法错误，不得悄悄转换为 `sample.sources`。旧 mode、旧 schema 和未知字段
由 Pydantic 明确拒绝。项目不提供 migrate 命令。

- [ ] **Step 2: Replace orchestration and delete obsolete code**

重写 `qresearch.pipeline.pipeline_research()` 使其只调用新内核，或在无调用者时删除旧函数并从 CLI 直接
调用 `run_research_strategy()`；两者只能选一个，不得保留 dispatcher。删除旧 CSV 参数处理、旧配置模型、
旧 event-inline factor 路径、兼容 adapter、fallback 和仅覆盖这些行为的测试。底层 ingest/signal/backtest
引擎若仍被新路径复用则保留。

- [ ] **Step 3: Verify the JSON envelope**

event 与 market 成功路径的 envelope `command` 都是 `pipeline.research`，并包含 sample kind、snapshot hash；
研究完成但经济门禁失败仍 exit 0、
status=blocked；数据或配置错误分别 exit 3/2。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_research_pipeline_cli.py tests/test_event_research_contract.py -q`  
Expected: event/market 单路由、旧参数拒绝和规范 contract 全部通过。

```bash
git add qresearch/cli.py qresearch/pipeline.py qresearch/config tests
git commit -m "feat: replace research pipeline with unified kernel"
```

---

### Task 4: Extend Gates, Reports, and Promotion Provenance

**Files:**
- Modify: `qresearch/engines/analysis/report.py`
- Modify: `qresearch/engines/analysis/templates/report_zh.html`
- Modify: `qresearch/engines/experiment/promote.py`
- Modify: `tests/test_report_zh.py`
- Create: `tests/test_research_promote.py`

- [ ] **Step 1: Define non-bypassable data gates**

任何新内核 run 缺少 feature snapshot hash、zer0share/source fingerprint、zer0factor identity、final holdout、
label coverage 或 split summary 时 `promotable=false`。这些属于数据正确性门禁，`--force` 只能绕过
现有经济门禁，不能绕过缺 lineage/PIT/holdout；event 和 market 没有例外分支。

- [ ] **Step 2: Extend the report**

中文报告增加：sample kind/universe、因子列表与 declared lag、snapshot hash、label 价格与 horizon、
train/validate/holdout 年、purged count、label status coverage、date-wise IC、mean_invested、历史涨跌停
成交假设。不得展示 HAC/FDR/PBO 字段或暗示它们已实现。

- [ ] **Step 3: Package unified provenance**

promote 复制 `feature_manifest.json`、`split_summary.json` 和 `evaluation_summary.json` 到 model package；
`provenance.json` 增加 sample kind、universe、snapshot hash 和三个代码/数据身份。不得复制整个
FeatureSnapshot 大表到 model package。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_report_zh.py tests/test_research_promote.py tests/test_registry_promote_wf.py -q`  
Expected: event/market 缺任一数据门禁均被阻止，且不存在绕过新 provenance 的旧路径。

```bash
git add qresearch/engines/analysis qresearch/engines/experiment/promote.py tests
git commit -m "feat: report and gate unified research runs"
```

---

### Task 5: Add Event/Market Config Skeletons and Capability Discovery

**Files:**
- Replace: `configs/examples/event_factors.yaml`
- Create: `configs/examples/market_factors.yaml`
- Modify: `qresearch/engines/experiment/scaffold.py`
- Modify: `qresearch/cli.py`
- Modify: `tests/test_config_new.py`
- Create: `tests/test_research_capabilities.py`

- [ ] **Step 1: Create a non-strategy example**

两个 example 都只包含新 schema；`features.refs=[]`、signals filters/rank 为空、evaluation 年为空。event
example 仅示意 `sample.sources`，market example 仅示意 universe/start/end。注释要求用户从 zer0factor
证据填写 factor name/lag，不提供可复制的因子组合或交易参数；不得保留旧字段注释。

- [ ] **Step 2: Reuse `config new`**

不新增 migrate 命令。用户通过现有命令指定模板：

```text
qr config new --from configs/examples/market_factors.yaml --out configs/experiments/<name>.yaml --study-id <id>
```

保持“只能从 examples 读取、只能写 experiments、signals 最后强制清空”的现有保护。

- [ ] **Step 3: Add `qr research capabilities`**

返回固定机器可读摘要：sample kinds supported=`event,market`；deferred=`index,custom,hybrid`；
feature providers supported=`zer0factor`；evaluation=`event_conditioned,cross_sectional,portfolio`；
advanced inference=`false`。该命令只报告能力，不探测或创建数据。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_config_new.py tests/test_research_capabilities.py -q`  
Expected: example 无 signals、scaffold 保护和 capability JSON 全部通过。

```bash
git add configs/examples/event_factors.yaml configs/examples/market_factors.yaml qresearch/engines/experiment/scaffold.py qresearch/cli.py tests
git commit -m "feat: scaffold supported market research configs"
```

---

### Task 6: Complete Four-Layer Documentation and Final Verification

**Files:**
- Modify: `.agents/skills/qresearch/SKILL.md`
- Modify: `.agents/skills/qresearch/factor-analysis.md`
- Modify: `.agents/skills/qresearch/backtest-optimize.md`
- Modify: `.agents/skills/qresearch/reference.md`
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `docs/superpowers/specs/2026-08-08-universal-research-kernel-design.md`

- [ ] **Step 1: Document supported and deferred boundaries**

所有文档一致写明：Iteration 3 的单内核支持 event 和 market，且没有旧版本兼容路径；index/custom/hybrid、全局
FactorArtifact、revision/vintage、HAC/FDR/PBO/CPCV、分钟线和通用 DAG 均未实现。Agent 遇到这些
请求先报告 unsupported，不得猜命令或构造空配置。

- [ ] **Step 2: Run focused gates**

Run: `python -m pytest tests/test_event_research_contract.py tests/test_research_pipeline_cli.py tests/test_research_backtest_pipeline.py tests/test_research_promote.py tests/test_research_capabilities.py -q`  
Expected: 全部通过。

- [ ] **Step 3: Run the complete suite**

Run: `python -m pytest -q --ignore=tests/test_protect_events_hook.py`  
Expected: 全部通过。

- [ ] **Step 4: Run local smoke commands**

Run: `python -m qresearch data ping --format json --quiet`  
Run: `python -m qresearch research capabilities --format json --quiet`  
Run: `python -m qresearch pipeline research --config <local_market_config> --format json --quiet`  
Expected: 前两个输出单 JSON；market 数据齐全时第三个完成 run，缺数据时 exit 3/5 并指出具体缺项。

- [ ] **Step 5: Confirm no deferred scaffolding exists**

Run: `rg -n "IndexSampleProvider|CustomSampleProvider|HAC|CPCV|FactorArtifact" qresearch`  
Expected: 无生产实现；允许文档或明确 unsupported capability 字符串。

- [ ] **Step 6: Confirm no compatibility or upstream edits exist**

Run: `rg -n "ResearchKernelConfig|legacy_inline|LegacyEvent|dispatch_pipeline_research|deprecated" qresearch configs`  
Expected: 无生产或配置命中。拒绝旧输入的测试可用字面量构造输入，但不得定义兼容实现。
Run: `git diff --name-only -- ../zer0share ../zer0factor`  
Expected: 无输出。若有输出，停止交付并撤销本迭代对这两个仓库的修改，不得提交。

- [ ] **Step 7: Commit**

```bash
git add qresearch tests configs/examples README.md ROADMAP.md .agents/skills/qresearch docs/superpowers
git commit -m "docs: complete unified research workflow"
```

## Iteration 3 Completion Gate

- event 和 market 均通过同一新内核；不存在旧 mode、旧 CLI 参数、兼容 adapter 或 fallback。
- 回测不重新查询 zer0factor。
- 历史涨跌停、T+1、成本和组合逻辑只有一套实现。
- 报告和 promote 包含 snapshot、split、coverage 和数据身份。
- capabilities 对未实现功能明确返回 deferred。
- 四层契约同步，全部非钩子测试通过。
- zer0share、zer0factor 仓库无代码、配置、数据布局或测试改动。

完成本迭代后，后续需求从 master index 的 deferred backlog 单独立项，不在本计划继续追加。
