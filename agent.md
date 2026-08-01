# qresearch Agent 开发原则与规范

面向在本仓库内改代码、补测试、扩 CLI 的 Agent / 开发者。目标：**迭代可预期、行为可回归、结构不腐化**。

研究闭环（因子分析 → 写策略 YAML → 回测 → 读报告）见 [`.cursor/skills/qresearch/SKILL.md`](.cursor/skills/qresearch/SKILL.md)。本文只管**工程与内核质量**，不替代该 skill。

---

## 1. 项目边界（不可破）

| 原则 | 要求 |
|------|------|
| 产品形态 | 仓库 = **确定性 CLI/库**；策略判断与研究剧本在 skill / 人，不塞进引擎「隐式智能」 |
| 行情来源 | 仅 zer0share（`ZER0SHARE_ROOT` / `ZER0SHARE_DATA`）；**禁止**引入 `vnpy` / `vnpy_portfoliostragtegy` |
| Agent I/O | CLI 机读输出：`--format json --quiet`；stdout = JSON 信封，日志走 stderr |
| 退出码 | `0` ok，`2` 配置，`3` 数据，`4` 门禁 blocked，`5` 依赖缺失 |
| 无隐式 +1 | 交易日延迟只许 `execution.lag_sessions`，禁止魔法「次日开盘」硬编码在别处 |

信封字段约定：`schema_version` / `ok` / `summary` / `artifacts` / `next_actions` / `error`。改信封属 **破坏性变更**，必须同步 README、skill reference，并补 envelope 单测。

---

## 2. 目录与职责

```
qresearch/                 # 可安装包：CLI + 引擎
  cli.py                   # 参数解析与编排入口，保持薄
  pipeline.py              # research/optimize 流水线
  config/models.py         # 唯一配置真源（Pydantic）
  engines/
    data/                  # ingest / panel / vendor / limitbook
    signal/                # 状态无关过滤与排序
    risk/                  # pretrade + PortfolioState
    backtest/              # session / sizing / costs
    factor/                # IC
    analysis/              # metrics / report / pit / overfit
    experiment/            # WF / optimize / promote / registry
    ops/                   # 实盘/信号意图（谨慎改）
  io/envelope.py           # Agent 结果信封
configs/examples/          # 示例模板：改前先复制
configs/experiments/       # 实验假设配置（可增，勿当唯一真源乱覆盖 examples）
tests/                     # 单测；合成 panel，默认不依赖 zer0share
workspace/                 # 本地工作区（gitignore；见 workspace/README.md）
  events/                  # 事件 CSV
  runs/                    # 实验 run 产物
  models/                  # 晋升后的模型包
  cache/                   # 行情 panel 缓存
```

**依赖方向**（勿反向引用）：

`config` → `data` → `signal` / `factor` → `risk` → `backtest` → `analysis` / `experiment` → `cli` / `pipeline`

- 回测引擎不得依赖 CLI、report HTML、Optuna。
- `vendor.py` 是唯一对外数据适配层；业务代码不直接 `import` zer0share 散落各处。

---

## 3. 领域术语（写配置与代码时必须用）

| 概念 | 正确键/字段 | 错误/仅 alias |
|------|-------------|---------------|
| 标的 | `instrument` | 业务逻辑里勿写死 `code` |
| 决策日 | `decision_date` | — |
| 计划入场/出场 | `entry_intent_date` / `exit_intent_date` | CSV 的 `buy_date`/`sell_date` 只在 ingest |
| 特征 | `features.*` | 原始列名经 alias 映射 |
| 仓位约束 | `portfolio.max_weight` / `max_new_entries_per_day` | — |
| 执行价 | `execution.price` ∈ `{open,close}` | 勿静默改成 VWAP 等 |
| 订单有效 | `order_validity_sessions`（1=GFD） | — |

退出优先级（`risk.exit_priority`）默认：`stop` → `take_profit` → `max_hold` → `exit_intent` → `deferred_exit`。调整须有测试，并在报告/文档说明。

日线成交语义（当前）：买入默认 open；`exit_intent` 卖用 close；止损/止盈用 high/low 触发 + open/触价代理。改价规则 = 行为变更，必须补 `tests/test_session_contracts.py`（或同级）用例。

---

## 4. 代码变更纪律

### 4.1 先小后大

1. **优先修局部**：在现有模块加函数/分支，禁止「顺手重写」无关文件。
2. **禁止臆造需求**：用户未要求的抽象层、插件框架、新配置方言一律不做。
3. **匹配现有风格**：`polars` + Pydantic v2 + dataclass 引擎状态；命名与周边文件一致。
4. **注释只解释非显然意图**（为何），不复述代码（做了什么）。

### 4.2 配置与兼容

- 新配置项放进 `qresearch/config/models.py`，给 **安全默认值**，旧 YAML 不破。
- 删除/改名配置键视为破坏性变更：保留别名迁移一期，或明确版本说明。
- **禁止**在引擎里读取未文档化的环境变量旁路配置（除已有 `ZER0SHARE_*` / settings）。
- 示例 `configs/examples/*`：先复制到 `configs/experiments/` 再改；不要把实验参数写回唯一示例而不留副本。

### 4.3 确定性与可复现

- 同一 CSV + 同一 config + 同一行情指纹 → 回测结果应稳定。
- 随机性（Optuna / shuffle IC）必须显式 `seed`。
- `workspace/runs/<run_id>/` 视为不可变产物：写入用 `RunWriter`；不要就地改历史 run 冒充新实验。
- 大表落盘到 `artifacts/`；对话与信封只引用路径和摘要指标。

### 4.4 错误处理

- 可预期的配置/数据问题：抛领域错误或返回信封 `error` + 正确退出码；禁止吞异常后返回空结果假装成功。
- 缺行情 / 缺依赖：失败并提示同步 zer0share，**禁止编造价格**。

---

## 5. 测试规范（质量闸门）

### 5.1 何时必须补测

| 变更类型 | 最低要求 |
|----------|----------|
| `engines/backtest/**`、成交价/退出/T+1/涨跌停 | 合成 panel 单测（参照 `tests/test_p0.py`、`test_session_contracts.py`） |
| `engines/signal/**`、`risk/**`、`costs`、`sizing` | 纯函数/状态单测 |
| `engines/data/ingest.py` | tmp CSV 或解析函数单测；勿只靠真实大 CSV |
| `io/envelope.py`、退出码语义 | JSON 信封解析单测 |
| `factor/ic`、metrics、overfit、WF purge | 数值/形状断言 |
| 仅改文案/HTML 样式 | 可只跑相关 report 测试 |
| `vendor` / 真实行情 | 标记 `@pytest.mark.e2e_local`，默认 CI/agent 循环不强制 |

### 5.2 测试风格

- 使用 `tests/conftest.py` 的 `sessions` / `panel` / `events`；需要极端行情时 **复制 bar 再改** `panel._by_key[...]`。
- 断言行为与原因字段（`reason`、`sell_blocked_*`、成交价），不只断言「有交易」。
- 单测不访问网络、不依赖本机 zer0share（e2e 除外）。
- 改完核心逻辑后至少：`pytest -q`。涉及回测时加上：`pytest -q tests/test_p0.py tests/test_session_contracts.py`。

### 5.3 覆盖率预期（务实）

- 不追求全库行覆盖；**交易契约与纯逻辑**优先。
- 回测/信号/费用/涨跌停相关改动后，相关模块覆盖不应明显倒退。
- CLI/`pipeline`/`ops` 可用薄集成或手动 JSON 冒烟；不要为刷覆盖率把 e2e 塞进默认套件。

---

## 6. CLI 与流水线

- 新命令：挂到 `qresearch/cli.py`，复用信封与退出码；help 文本简洁。
- 编排逻辑放 `pipeline.py` 或 `engines/*`，CLI 只做参数 → 调用 → `emit`。
- 破坏性 CLI 改名需保留旧别名一段时间（若已有用户/skill 依赖）。
- skill / README / `reference.md` 中的命令表与行为必须同步更新。

---

## 7. 明确禁止

1. 引入重型交易框架或第二套回测引擎「并存」。
2. 在 `signal` 层读取持仓/现金（信号必须状态无关；配额在 `pretrade`）。
3. 为方便测试而改生产默认语义（例如关掉 T+1）。
4. 提交密钥、`.env`、本地数据绝对路径写死进库。
5. 大段注释掉的死代码、未使用的宽泛 try/except、无必要的兼容适配层。
6. 未经要求的 markdown 文档膨胀（本文与 skill 更新除外）。
7. 用「优化可读性」做无关重构搭车。

---

## 8. 推荐工作流（改内核时）

```
Task Progress:
- [ ] 1. 定位模块与现有测试（先读再改）
- [ ] 2. 最小改动实现；配置走 models.py 默认值
- [ ] 3. 补/改单测（合成数据）
- [ ] 4. pytest -q（相关文件 + 全量）
- [ ] 5. 若影响 CLI/领域语义：更新 README 或 skill reference
- [ ] 6. 回复用户：行为变化、风险、如何验证
```

研究类任务（跑因子/回测/调参）走 qresearch skill，不要把研究产出硬编码进 `engines/`。

---

## 9. 审查清单（PR / 交付前自检）

- [ ] 领域词用对了（intent dates / features.* / lag_sessions）
- [ ] 无 vnpy；行情仍只经 vendor
- [ ] 旧 YAML / 旧信封仍能工作，或已说明迁移
- [ ] 行为变化有测试；`pytest -q` 通过
- [ ] 未改坏 `configs/examples` 唯一模板（或已复制）
- [ ] 无无关重构；无编造行情
- [ ] 文档/skill 与命令行为一致

---

## 10. 关键文件速查

| 主题 | 路径 |
|------|------|
| 回测成交与退出 | `qresearch/engines/backtest/session.py` |
| 配置模型 | `qresearch/config/models.py` |
| 事件入库 | `qresearch/engines/data/ingest.py` |
| 信封 | `qresearch/io/envelope.py` |
| 回测契约测试 | `tests/test_p0.py`, `tests/test_session_contracts.py` |
| 研究 Agent 剧本 | `.cursor/skills/qresearch/SKILL.md` |
| CLI 参考 | `.cursor/skills/qresearch/reference.md` |

维护原则：**扩展靠加测试与清晰边界，不靠堆特例。** 若某次改动需要同时改超过 3 个无关子系统才能「跑通」，先停下来重新设计落点，而不是继续摊大饼。
