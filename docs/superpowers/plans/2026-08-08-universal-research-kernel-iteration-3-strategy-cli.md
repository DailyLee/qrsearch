# Universal Research Kernel Iteration 3: Strategy, Backtest, and CLI Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Iteration 2 的 market ResearchDataset 接入信号、日频回测、报告和 promote 门禁，并删除旧 event/CSV 研究路径。

**Architecture:** 不重写 signal/backtest；新增 market strategy adapter，把市场观察转换为现有 `build_ranked()` 和 `run_backtest()` 所需内部列。Iteration 2 已删除 event/CSV 公共面；本迭代只在 zer0share universe + zer0factor dataset 上恢复策略、回测和优化命令。

**Tech Stack:** Python 3.11、Pydantic 2、Polars、Typer、Jinja2、pytest、Iteration 1/2 产物。

## Prerequisite

- Iteration 1 和 Iteration 2 completion gate 全部通过。
- `qr research materialize/evaluate` 已能生成固定 snapshot/dataset，并通过 zer0factor EvaluationService
  生成 train-only 因子分析产物。

## Global Constraints

- 配置没有 `sample.kind` 或 `sample.sources`；样本固定来自 zer0share universe，CLI 不接受 `--csv`。
- 不保留 event provider、kernel mode、旧 YAML adapter、deprecated option、双路径 dispatcher 或 fallback。
- zer0share、zer0factor 仓库只读；本迭代所有实现和适配只能落在 qrsearch。
- signal/backtest 不重新查询 zer0factor，只读取 run 内 `dataset.parquet`。
- 复用现有 `risk.max_hold_sessions` 作为唯一固定持有期；不新增 holding 类型层级或 event exit 语义。
- 成交使用 Iteration 1 的历史 `up_limit/down_limit` 和停牌规则。
- `best_value` 不等于 promotable；继续执行现有 quality gates、sensitivity 和 holdout 纪律。
- 不实现配置迁移、index/custom、FactorArtifact、DAG、HAC/FDR/PBO。
- 不创建空 Provider 或返回空成功信封；不支持即 exit 2。

---

### Task 1: Adapt Market Observations to Existing Signal Inputs

**Files:**
- Create: `qresearch/research/strategy.py`
- Create: `tests/test_market_strategy_adapter.py`
- Modify: `qresearch/config/models.py`

**Interfaces:**

```text
build_market_signal_frame(dataset: ResearchDataset, config: ResearchConfig, calendar: list[date]) -> pl.DataFrame
```

输出列固定为：`instrument, decision_date, entry_intent_date, exit_intent_date` 加全部 `features.*`。
`decision_date=asof_session`、`entry_intent_date=effective_session`；`exit_intent_date` 按交易日历后移
`risk.max_hold_sessions`。这些 event 风格列只是复用现有 signal/backtest 的内部 adapter schema，不能作为
CSV/event 公共契约。不允许猜测默认持有期。

- [ ] **Step 1: Write mapping tests**

覆盖日期映射、feature 列保留、同一股票不同日观察、末端无退出 session、缺/非法 max_hold 和重复观察键。
末端无退出 session 的行删除并返回计数。

- [ ] **Step 2: Add fixed-holding validation**

要求 `risk.max_hold_sessions >= 1` 且 `risk.exit_priority` 包含 `max_hold`。配置模型拒绝 event exit 和
mode 字段，不提供 alias。

- [ ] **Step 3: Implement without signal duplication**

adapter 只做列映射和日期移动，随后直接调用现有 `build_ranked(frame, config)`；不得复制 filters、
rank、composite 实现。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_market_strategy_adapter.py tests/test_signal_engine.py -q`
Expected: 全部通过。

```bash
git add qresearch/research/strategy.py qresearch/config/models.py tests/test_market_strategy_adapter.py
git commit -m "feat: adapt market research datasets to signals"
```

---

### Task 2: Run the Existing Backtest from a Frozen Market Dataset

**Files:**
- Modify: `qresearch/research/pipeline.py`
- Create: `tests/test_research_backtest_pipeline.py`

**Interfaces:**

```text
run_research_strategy(config_path: str | Path, run_id: str | None = None, n_trials_assumed: int | None = None) -> dict[str, object]
```

- [ ] **Step 1: Write the no-requery contract**

先 materialize dataset，再 monkeypatch Zer0FactorFeatureProvider 使任何后续调用都抛异常；
`run_research_strategy()` 必须仍能从 `artifacts/dataset.parquet` 完成信号和回测。使用一个 market
合成用例，并断言写出
`ranked_signals.parquet, equity.csv, trades.csv, metrics.json, rejects_summary.json`。不得继续暴露
`ranked_events.parquet` 旧产物名。

- [ ] **Step 2: Reuse the existing backtest path**

`run_research_strategy()` 先以同一 run_id 调用 `materialize_research()`，随后从该 run 的
`artifacts/dataset.parquet` 重新读取冻结数据；再使用 `build_market_signal_frame()` → `build_ranked()` →
`load_price_panel()` → `run_backtest()`。
metrics 继续调用 `attach_overfit_metrics()` 和 `mean_invested_from_equity()`；不得建立第二套成本、
组合、T+1、涨跌停或退出逻辑。

- [ ] **Step 3: Attach unified provenance**

meta 至少写 `sample_kind=market`、universe、feature snapshot hash、label spec、split summary、
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

### Task 3: Restore `pipeline research` and Optimization on the Market Kernel

**Files:**
- Modify: `qresearch/cli.py`
- Modify: `qresearch/pipeline.py`
- Create: `tests/test_research_pipeline_cli.py`
- Create: `tests/test_market_optimization_pipeline.py`
- Modify: `tests/test_no_csv_surface.py`

**Interfaces:**

research 主入口：

```text
pipeline_research(config_path: str | Path, run_id: str | None, n_trials_assumed: int | None) -> dict[str, object]
qr pipeline research --config <yaml> [--run-id <id>] [--n-trials-assumed <n>]
```

- [ ] **Step 1: Write the breaking CLI contract first**

测试 market 配置调用 `run_research_strategy()`。`--config` 必填；删除 `--csv` option，传入 `--csv` 应由
Typer 返回配置/用法错误。旧 mode、kind、sources、event schema 和未知字段
由 Pydantic 明确拒绝。项目不提供 migrate 命令。

- [ ] **Step 2: Replace orchestration**

在 Iteration 2 已删除旧入口的基础上新增 `qresearch.pipeline.pipeline_research()`，使其只调用
`run_research_strategy()`；CLI 不得增加 dispatcher、CSV 参数、兼容 adapter 或 fallback。

- [ ] **Step 3: Restore optimization on the frozen market dataset**

重新提供 `pipeline optimize/sweep/sensitivity`，输入为
`config_path/--config`，统一先调用 `materialize_research()` 并只读取当前 run 的 `dataset.parquet`；优化循环
不得重新读取 zer0share 或 zer0factor。公开函数签名固定为：

```text
pipeline_optimize(config_path: str | Path, *, feature: str | None = None, side: str = "auto", keep_frac: str = "0.1,0.2,0.3,0.4", n_trials: int | None = None) -> dict[str, object]
pipeline_sweep(config_path: str | Path, *, set_specs: list[str], metric: str = "sharpe", max_grid: int = 64) -> dict[str, object]
pipeline_sensitivity(config_path: str | Path, *, cost_mult: str = "1,1.5,2", stop: str = "-0.05,-0.086,-0.12", take: str = "0.10,0.158,0.20", max_hold: str | None = None, max_weight: str | None = None, max_new: str | None = None, sizing_base: str | None = None, max_names_per_industry: str | None = None, max_new_per_industry: str | None = None, max_grid: int = 64) -> dict[str, object]
```

`tests/test_market_optimization_pipeline.py` 分别断言三个命令只物化一次 dataset，参数网格循环期间不调用
zer0share/zer0factor，并写出原有 summary/grid artifact。`tests/test_no_csv_surface.py` 再次扫描所有新增入口。

- [ ] **Step 4: Verify the JSON envelope**

成功 envelope 的 `command` 是 `pipeline.research`，并包含 `sample_kind=market`、snapshot hash；
研究完成但经济门禁失败仍 exit 0、
status=blocked；数据或配置错误分别 exit 3/2。

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/test_research_pipeline_cli.py tests/test_market_optimization_pipeline.py tests/test_no_csv_surface.py -q`
Expected: market 单路由、旧参数拒绝、三个优化命令读取冻结 dataset 和零 CSV 公共面全部通过。

```bash
git add qresearch/cli.py qresearch/pipeline.py tests/test_research_pipeline_cli.py tests/test_market_optimization_pipeline.py tests/test_no_csv_surface.py
git commit -m "feat: add market research and optimization commands"
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

任何 run 缺少 feature snapshot hash、zer0share fingerprint、zer0factor identity、factor screening manifest、final holdout、
label coverage 或 split summary 时 `promotable=false`。这些属于数据正确性门禁，`--force` 只能绕过
现有经济门禁，不能绕过缺 lineage/PIT/holdout；不存在其他样本路径的例外分支。

- [ ] **Step 2: Extend the report**

中文报告增加：sample kind/universe、因子列表与 declared lag、snapshot hash、label 价格与 horizon、
train/validate/holdout 年、purged count、label status coverage、zer0factor train screening 摘要与链接、
factor redundancy、mean_invested、历史涨跌停
成交假设。不得展示 HAC/FDR/PBO 字段或暗示它们已实现。

- [ ] **Step 3: Package unified provenance**

promote 复制 `feature_manifest.json`、`split_summary.json`、`factor_screening_manifest.json` 和 zer0factor
summary/report 到 model package；
`provenance.json` 增加 sample kind、universe、snapshot hash 和三个代码/数据身份。不得复制整个
FeatureSnapshot 大表到 model package。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_report_zh.py tests/test_research_promote.py tests/test_registry_promote_wf.py -q`  
Expected: market run 缺任一数据门禁均被阻止，且不存在绕过新 provenance 的旧路径。

```bash
git add qresearch/engines/analysis qresearch/engines/experiment/promote.py tests
git commit -m "feat: report and gate unified research runs"
```

---

### Task 5: Replace the Example and Remove Unneeded Capability Discovery

**Files:**
- Delete: `configs/examples/event_factors.yaml`
- Create: `configs/examples/market_factors.yaml`
- Modify: `qresearch/engines/experiment/scaffold.py`
- Modify: `tests/test_config_new.py`

- [ ] **Step 1: Create a non-strategy example**

唯一的 market example 只包含新 schema；`features.refs=[]`、signals filters/rank 为空、evaluation 年为空，
sample 只示意 universe/start/end。注释要求用户从 zer0factor 证据填写 factor name/lag，不提供可复制的
因子组合或交易参数；删除 event example，不保留旧字段注释。

- [ ] **Step 2: Reuse `config new`**

不新增 migrate 命令。用户通过现有命令指定模板：

```text
qr config new --from configs/examples/market_factors.yaml --out configs/experiments/<name>.yaml --study-id <id>
```

保持“只能从 examples 读取、只能写 experiments、signals 最后强制清空”的现有保护。

- [ ] **Step 3: Do not add capability discovery**

只有一条固定路径时，`qr research capabilities` 只会重复 README 和配置 schema，属于过度设计。本迭代不创建
该命令、registry 或 capability 枚举；不支持的字段直接由配置校验拒绝。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_config_new.py -q`
Expected: example 无 signals、scaffold 保护、唯一模板和旧 event 模板不存在的断言全部通过。

```bash
git add -A configs/examples qresearch/engines/experiment/scaffold.py tests/test_config_new.py
git commit -m "feat: make market research the only config template"
```

---

### Task 6: Complete Four-Layer Documentation and Final Verification

**Files:**
- Modify: `AGENTS.md`
- Modify: `.agents/skills/qresearch/SKILL.md`
- Modify: `.agents/skills/qresearch/factor-analysis.md`
- Modify: `.agents/skills/qresearch/backtest-optimize.md`
- Modify: `.agents/skills/qresearch/reference.md`
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `docs/superpowers/specs/2026-08-08-universal-research-kernel-design.md`

- [ ] **Step 1: Document supported and deferred boundaries**

`AGENTS.md`、README、ROADMAP 和 qresearch skill 全部改为 market 研究术语：样本由 zer0share universe
产生，因子由 zer0factor 产生，研究命令只接受 config。删除事件研究闭环、`--csv` 年份对齐、
validate-events 和 event exit 指令。所有文档一致写明：Iteration 3 只支持 zer0share universe + zer0factor；
event/CSV/index/custom/hybrid、全局
FactorArtifact、revision/vintage、HAC/FDR/PBO/CPCV、分钟线和通用 DAG 均未实现。Agent 遇到这些
请求先报告 unsupported，不得猜命令或构造空配置。

- [ ] **Step 2: Run focused gates**

Run: `python -m pytest tests/test_research_pipeline_cli.py tests/test_no_csv_surface.py tests/test_research_backtest_pipeline.py tests/test_research_promote.py tests/test_config_new.py -q`
Expected: 全部通过。

- [ ] **Step 3: Run the complete suite**

Run: `python -m pytest -q --ignore=tests/test_protect_events_hook.py`  
Expected: 全部通过。

- [ ] **Step 4: Run local smoke commands**

Run: `python -m qresearch data ping --format json --quiet`  
Run: `python -m qresearch pipeline research --config <local_market_config> --format json --quiet`  
Expected: data ping 输出单 JSON；market 数据齐全时 research 完成 run，缺数据时 exit 3/5 并指出具体缺项。

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
git add qresearch tests configs/examples AGENTS.md README.md ROADMAP.md .agents/skills/qresearch docs/superpowers
git commit -m "docs: complete unified research workflow"
```

## Iteration 3 Completion Gate

- 只有 zer0share universe + zer0factor market 路径；不存在 event/CSV、kind/mode、兼容 adapter 或 fallback。
- 回测不重新查询 zer0factor。
- 历史涨跌停、T+1、成本和组合逻辑只有一套实现。
- 报告和 promote 包含 snapshot、split、coverage 和数据身份。
- 配置 schema 直接拒绝 event/CSV 和未实现样本类型；没有 capability registry 空架子。
- 四层契约同步，全部非钩子测试通过。
- zer0share、zer0factor 仓库无代码、配置、数据布局或测试改动。

完成本迭代后，后续需求从 master index 的 deferred backlog 单独立项，不在本计划继续追加。
