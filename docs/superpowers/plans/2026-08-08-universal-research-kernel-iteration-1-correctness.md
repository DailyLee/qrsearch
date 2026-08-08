# Universal Research Kernel Iteration 1: Correctness Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正事件研究路径中会污染后续通用化的确定性错误，并冻结新内核必须满足的规范基线。

**Architecture:** 本迭代不建立新研究内核。只在现有 `engines`、`pipeline.py` 和测试体系内修正 IC、历史涨跌停成交约束和缓存 lineage。这里冻结的是期望业务语义，不是旧接口兼容承诺；Iteration 3 可删除或替换旧 CLI、YAML 和编排代码。

**Tech Stack:** Python 3.11、Pydantic 2、Polars、NumPy、Typer、pytest、zer0share LocalPro。

## Global Constraints

- 工作目录默认为 `C:\Users\dl271\Downloads\code\qrsearch`。
- 不修改 `workspace/events/**`、`workspace/events_ascii/**`。
- zer0share、zer0factor 仓库均只读；本迭代所有改动只能落在 qrsearch。缺少上游字段或 API 时明确报错，
  不得修改上游、复制上游实现或构造数据。
- 不新增兼容层、deprecated alias、双路径或版本 mode；发现只为旧版本保留的分支时，在其替代实现落地的
  迭代中直接删除。
- 不引入 vnpy，不创建新回测引擎。
- 行情和每日涨跌停只读取 zer0share；禁止按股票代码自行推导 10%/20%/ST 规则。
- Agent I/O 保持 `--format json --quiet`、单 JSON 信封和退出码 0/2/3/4/5。
- 本迭代不新增 `qresearch/research/`、SampleProvider、zer0factor Provider 或 universal CLI。
- 行为修复同时同步测试、`.agents/skills/qresearch/` 对应说明和 README。

## Deliverables

- 事件研究规范 golden contract。
- ties/NaN 正确的 `spearman_ic()`。
- PricePanel 携带 zer0share 历史 `up_limit/down_limit`，回测不再使用固定百分比 LimitBook。
- 价格缓存命中时保留真实源数据指纹。
- 所有现有非钩子测试及本迭代新增测试通过。

## Explicit Non-Goals

- 不实现 market/index/custom 样本。
- 不实现全局不可变 FactorArtifact。
- 不实现 HAC、FDR、PBO、CPCV。
- 不实现成交排队、盘口、分钟线或冲击模型。
- 不修改 stop/take 的既有日线触发规则；仅在文档中继续声明其启发式性质。

---

### Task 1: Freeze the Normative Event Research Contract

**Files:**
- Modify: `tests/fixtures/make_synth.py`
- Create: `tests/golden/event_research_contract.json`
- Create: `tests/test_event_research_contract.py`

**Interfaces:**
- Consumes: `qresearch.pipeline.pipeline_research()`、`tests.fixtures.make_synth`。
- Produces: `run_event_contract_case(tmp_path: Path) -> dict[str, object]` 和稳定 golden JSON。

- [ ] **Step 1: Add a deterministic fixture**

在 `tests/fixtures/make_synth.py` 增加 `make_event_contract_case()`。返回值固定为
`tuple[pl.DataFrame, pl.DataFrame, list[date]]`，事件包含两年、至少六只股票、重复事件、缺 bar、
连续因子和离散因子；bars 明确带 `up_limit/down_limit`，不依赖 zer0share 本地数据。

- [ ] **Step 2: Add the contract runner and assertions**

`tests/test_event_research_contract.py` 必须比较以下稳定字段：

```python
assert result["sample_keys"] == golden["sample_keys"]
assert result["ranked_keys"] == golden["ranked_keys"]
assert result["trade_keys"] == golden["trade_keys"]
assert result["metrics"] == pytest.approx(golden["metrics"], rel=1e-10, abs=1e-12)
```

测试 monkeypatch `qresearch.pipeline.load_events` 返回 fixture events，monkeypatch
`qresearch.pipeline.load_price_panel` 返回用 `bars_override/calendar_override` 构建的 PricePanel，并把
`AppSettings.runs_dir` 指向 `tmp_path/runs`；不得读取真实 events 或网络。fixture 的开盘价远离
up/down limit，后续正确性修复不应改变该 golden。

- [ ] **Step 3: Generate and review the golden once**

Run: `python -m pytest tests/test_event_research_contract.py -q`  
Expected: 第一次因 golden 不存在而失败；由测试辅助函数输出候选 payload。人工确认 sample、ranked、trade
键后写入 JSON，再次运行通过。禁止在测试运行时自动更新 golden。

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/make_synth.py tests/golden/event_research_contract.json tests/test_event_research_contract.py
git commit -m "test: freeze normative event research contract"
```

---

### Task 2: Correct Spearman Ties and Non-Finite Handling

**Files:**
- Modify: `qresearch/engines/factor/ic.py`
- Modify: `tests/test_factor_ic.py`

**Interfaces:**
- Keeps: `spearman_ic(x: np.ndarray, y: np.ndarray) -> float`。
- New private helper: `_average_ranks(values: np.ndarray) -> np.ndarray`。

- [ ] **Step 1: Write regression tests**

加入三个用例：ties 使用平均秩；NaN/inf 成对删除；删除后不足三对返回 NaN。ties 用例必须和
`pandas.Series.rank(method="average").corr(..., method="pearson")` 的固定期望一致，但生产依赖不新增 pandas。

```python
x = np.array([1.0, 1.0, 2.0, 3.0])
y = np.array([1.0, 2.0, 2.0, 4.0])
assert spearman_ic(x, y) == pytest.approx(0.8333333333333335)
```

- [ ] **Step 2: Run the focused test and observe failure**

Run: `python -m pytest tests/test_factor_ic.py -q`  
Expected: ties 或 non-finite 新用例失败，证明旧双 `argsort()` 行为被覆盖。

- [ ] **Step 3: Implement average ranks**

`spearman_ic()` 先把输入转为 float 数组，以 `np.isfinite(x) & np.isfinite(y)` 成对过滤，再稳定排序；
每个相等值区间写入该区间首尾秩的平均值。不得调用 SciPy，不改变公开签名。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_factor_ic.py tests/test_factor_hardening.py -q`  
Expected: 全部通过。

```bash
git add qresearch/engines/factor/ic.py tests/test_factor_ic.py
git commit -m "fix: handle ties and non-finite factor pairs"
```

---

### Task 3: Load Historical Price-Limit Facts from zer0share

**Files:**
- Modify: `qresearch/engines/data/vendor.py`
- Modify: `qresearch/engines/data/panel.py`
- Modify: `tests/test_panel_and_wf.py`
- Create: `tests/test_vendor_limits.py`

**Interfaces:**
- Keeps: `load_daily_long(...) -> tuple[pl.DataFrame, str]`。
- Extends returned schema with nullable `up_limit: Float64` and `down_limit: Float64`。

- [ ] **Step 1: Write the vendor contract test**

Monkeypatch `get_local_pro()` with a fake object exposing `daily()`、`adj_factor()`、`stk_limit()`。
断言 `load_daily_long()` 按 `ts_code + trade_date` 合并历史限制价格，并将其重命名后的股票代码保持在
`instrument`。测试同时断言 adj_factor 只 `ffill`，第一条缺失值不得从未来回填。

- [ ] **Step 2: Extend the empty and loaded schemas**

在 `_EMPTY_BARS` 增加 `up_limit/down_limit`。对不适用股票涨跌停的指数 loader 写入 null，而不是
虚构比例。`load_daily_long()` 批量调用 `pro.stk_limit()`，字段只取
`ts_code,trade_date,up_limit,down_limit`。将 `_cache_key()` schema 前缀从 `pit_raw_v1` 改为
`pit_raw_v2`，使旧的无涨跌停列缓存不会被读取。

- [ ] **Step 3: Define missing-data behavior**

真实股票 bar 缺少当日限制价格时保留 null；loader 不推导百分比。返回 fingerprint 必须同时覆盖
zer0share 的 daily、adj_factor 和 stk_limit Parquet 路径。成交阶段负责返回
`missing_limit_data`，不把 null 当成可成交。合成 `bars_override` 测试必须显式提供限制价格。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_vendor_limits.py tests/test_panel_and_wf.py tests/test_pit_qfq.py -q`  
Expected: 全部通过。

```bash
git add qresearch/engines/data/vendor.py qresearch/engines/data/panel.py tests/test_vendor_limits.py tests/test_panel_and_wf.py
git commit -m "feat: load historical stock price limits"
```

---

### Task 4: Replace Percentage Heuristics with Historical Limits

**Files:**
- Modify: `qresearch/engines/data/limitbook.py`
- Modify: `qresearch/engines/risk/pretrade.py`
- Modify: `qresearch/engines/backtest/session.py`
- Modify: `qresearch/engines/ops/runner.py`
- Modify: `tests/test_costs_and_limits.py`
- Modify: `tests/test_session_contracts.py`

**Interfaces:**
- Replace constructor: `LimitBook()`；删除 `up_pct/down_pct` 参数。
- Replace calls: `can_buy_open(bar)` and `can_sell_open(bar)`；删除 `prev_close` 参数。

- [ ] **Step 1: Write exact behavior tests**

用例必须覆盖：open 等于 up_limit 时买入拒绝 `limit_up`；open 等于 down_limit 时卖出拒绝
`limit_down`；vol=0 返回 `suspended`；bar=None 返回 `data_gap`；限制字段为 null 返回
`missing_limit_data`；普通开盘允许成交。另加一只历史 up_limit 为 12.34 的股票，证明实现没有
按 10%/20% 重算。

- [ ] **Step 2: Run tests and confirm old API fails**

Run: `python -m pytest tests/test_costs_and_limits.py tests/test_session_contracts.py -q`  
Expected: 新签名和历史限制价格用例失败。

- [ ] **Step 3: Implement and update every caller**

`LimitBook` 只读取 bar 内的 `open/vol/up_limit/down_limit`。更新 pretrade、backtest、ops 和测试中
所有调用；使用 `rg -n "can_buy_open|can_sell_open|LimitBook\(" qresearch tests` 确认没有旧参数调用。

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest tests/test_costs_and_limits.py tests/test_session_contracts.py tests/test_industry_cap.py tests/test_p0.py -q`  
Expected: 全部通过。

```bash
git add qresearch/engines/data/limitbook.py qresearch/engines/risk/pretrade.py qresearch/engines/backtest/session.py qresearch/engines/ops/runner.py tests
git commit -m "fix: use historical price limits for fills"
```

---

### Task 5: Preserve Data Fingerprints on Price-Cache Hits

**Files:**
- Modify: `qresearch/engines/data/panel.py`
- Create: `tests/test_panel_cache_lineage.py`

**Interfaces:**
- Cache files remain `<key>.parquet`。
- Add sidecar `<key>.meta.json` with schema `{"data_fingerprint": str, "cache_key": str}`。

- [ ] **Step 1: Write miss/hit tests**

第一次调用 monkeypatch 后的 `vendor.load_daily_long()` 返回 fingerprint `source-fp-1`；第二次调用必须
只读缓存且 `panel.data_fingerprint == "source-fp-1"`。只有 parquet 没有 sidecar 时视为旧缓存，
重新加载并补写 sidecar，禁止返回 `cache_hit`。

- [ ] **Step 2: Implement atomic sidecar writes**

parquet 写完后先写同目录临时 JSON，再 `Path.replace()` 到正式 sidecar。读取 JSON 缺字段、损坏或
cache_key 不一致时忽略旧缓存并重新加载。不得删除用户整个 cache 目录。

- [ ] **Step 3: Verify and commit**

Run: `python -m pytest tests/test_panel_cache_lineage.py tests/test_panel_and_wf.py -q`  
Expected: miss/hit/损坏 sidecar 三个路径通过。

```bash
git add qresearch/engines/data/panel.py tests/test_panel_cache_lineage.py
git commit -m "fix: retain source fingerprint across cache hits"
```

---

### Task 6: Close Iteration 1 Across Tests, Skill, and README

**Files:**
- Modify: `.agents/skills/qresearch/reference.md`
- Modify: `.agents/skills/qresearch/backtest-optimize.md`
- Modify: `README.md`
- Modify: `ROADMAP.md`

- [ ] **Step 1: Document the exact execution assumption**

写明：日频开盘成交使用 zer0share 当日 `up_limit/down_limit`；开盘触及涨停不买、触及跌停不卖；
停牌不成交；不模拟排队、开板时点和盘口。因子 IC/理论 forward return 不使用该成交过滤。

- [ ] **Step 2: Run the iteration gate**

Run: `python -m pytest -q --ignore=tests/test_protect_events_hook.py`  
Expected: 全部通过。  
Run: `python -m qresearch data ping --format json --quiet`  
Expected: stdout 为单 JSON，`summary.import_ok=true`；本地数据缺失则以退出码 5 和明确 dependency
错误记录，不伪造通过。

- [ ] **Step 3: Confirm scope**

Run: `git diff --name-only`  
Expected: 不包含 `qresearch/research/`、`workspace/events/**`、zer0share/zer0factor 仓库文件或 universal CLI。

- [ ] **Step 4: Commit**

```bash
git add README.md ROADMAP.md .agents/skills/qresearch tests qresearch
git commit -m "docs: record historical-limit backtest contract"
```

## Iteration 1 Completion Gate

- 事件研究规范 golden contract 通过；该 contract 约束业务结果，不约束旧接口继续存在。
- Spearman ties/NaN 用例通过。
- 回测只消费历史 `up_limit/down_limit`，不存在固定 10%/20% 推导。
- 缓存命中保留真实 fingerprint。
- 全部非钩子测试通过。
- 未实现任何 Iteration 2/3 功能。

满足全部条件后才开始 Iteration 2。
