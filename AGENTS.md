# qresearch 工程与 Agent 规范

本文件是本仓库唯一的 Agent / 开发者工程契约，供 Codex 自动读取。能力演进与优先级见 [ROADMAP.md](ROADMAP.md)。研究闭环（因子 → 策略 YAML → 回测 → 质量闸门 → 迭代）见 [`.agents/skills/qresearch/SKILL.md`](.agents/skills/qresearch/SKILL.md) 与 [quality-gates.md](.agents/skills/qresearch/quality-gates.md)。

## 四位一体对齐（强制）

任何修改或新增（配置字段、CLI、信封字段、引擎语义、研究约定）在合并/交付前，必须同时对齐下列四层；缺一层即未完成：

| 层 | 落点 | 要对齐什么 |
|----|------|------------|
| **配置** | `qresearch/config/models.py`；必要时 `configs/examples/*` 骨架（勿塞策略信号） | 字段名、类型、安全默认、YAML 示意 |
| **用例** | `tests/**`；改成交/退出/涨跌停等 → 合成 panel 单测 | 默认行为、边界、回归；相关用例 `pytest -q` 通过 |
| **Skill** | `.agents/skills/qresearch/`（至少触及的 `SKILL.md` / `reference.md` / 专题 md） | Agent 可执行约定、命令、禁区、研究分流 |
| **文档 md** | 根目录 `README.md`；破坏契约时还有本文 / `ROADMAP.md` 状态行 | 用户可见 CLI、全局旗标、领域词、快速上手 |

### 触发与最小同步面

| 改动类型 | 至少同步 |
|----------|----------|
| 新/改 `config/models` 字段或默认 | 示例 YAML 注释或字段；单测；skill 中配置表/约定 |
| 新/改 CLI 或全局旗标、退出码、JSON 信封 | `README.md` + skill `reference.md`；信封/解析单测 |
| 引擎语义（ingest / 信号 / 回测 / 门禁） | 单测；skill 中对应专题；若改领域词则 README 表 |
| 仅内部重构、无对外行为变化 | 单测保持绿；**勿**为文档而文档 |

### 完成自检（PR / 会话收尾）

- [ ] 配置默认与示例一致，无旧别名/双词表残留
- [ ] 有对应用例覆盖默认与关键边界
- [ ] skill 无过时命令或矛盾约定
- [ ] README / 相关 md 与对外行为一致
- [ ] 未改事件只读区 `workspace/events/**`、`workspace/events_ascii/**`

禁止：只改代码不改约定；只改 skill 不改实现；示例里塞可抄的「假策略」signals；通读 `configs/experiments/` 当知识库。

## 硬约束（产品边界）

- 仓库 = 确定性 CLI/库；**勿引入 vnpy**；行情仅 zer0share / `engines/data/vendor.py`。
- Agent I/O：`--format json --quiet`；stdout JSON 信封；退出码 0/2/3/4/5（research 门禁失败常仍为 0，看信封）。
- 无隐式 +1：延迟只用 `execution.lag_sessions`。
- 领域词：`entry_intent_date` / `exit_intent_date` / `features.*`；CSV `buy_date`/`code` 仅 ingest alias。
- 信号层状态无关；持仓配额在 `pretrade`；回测不依赖 CLI/HTML/Optuna。
- **事件原始数据只读**：禁止改/删/覆盖 `workspace/events/**` 与 `workspace/events_ascii/**`。仅 `--csv` 读取；衍生只写 `workspace/runs/`。`.codex/hooks.json` 只提供额外保护，不改变这项约束。
- 板块分流：`ingest.board` = `limit10`（默认）| `limit20` | `all`；`limit20` = 科创 688/689 + 创业 300/301。两类分开研究。
- 研究样本：充分利用 events 实际覆盖（见 `sample_profile.years` / `years_span`）；勿因截断丢年；full 须覆盖本次全部可用年（细则见 skill）。

## 改代码时

- 最小改动；禁止搭车重构。
- 新配置进 `config/models.py` 且带安全默认。
- 改 `engines/backtest/**` 或成交/退出/涨跌停语义 → 必须补合成 panel 单测，并跑 `pytest -q`。
- 勿覆盖唯一的 `configs/examples/*` 当作实验；开局用 `qr config new` 写 `configs/experiments/`；迭代用 `qr config apply-best`。
- 勿编造行情；缺数据就失败并提示同步 zer0share。
- 实验配置是落盘区非知识库：勿开局通读，除非用户指定或 study 决策链指向。

## 文档地图

| 文档 | 用途 |
|------|------|
| [AGENTS.md](AGENTS.md) | 本文：工程契约与四位一体对齐 |
| [README.md](README.md) | 安装、快速上手、CLI 摘要、领域词 |
| [ROADMAP.md](ROADMAP.md) | 能力演进与优先级 |
| [.agents/skills/qresearch/SKILL.md](.agents/skills/qresearch/SKILL.md) | 研究 Agent 主流程 |
| [.agents/skills/qresearch/quality-gates.md](.agents/skills/qresearch/quality-gates.md) | 定稿硬否决（密度/退出/仓位/多目标） |
| [.agents/skills/qresearch/research-loop.md](.agents/skills/qresearch/research-loop.md) | 分支 / 停手 / **反模式唯一清单** |
| [.agents/skills/qresearch/reference.md](.agents/skills/qresearch/reference.md) | CLI / ingest / 信封速查 |
