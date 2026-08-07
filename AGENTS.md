# Agents

本仓库以 **Codex 优先**，同时保留 Cursor 兼容性。

## 必须遵守

- 完整工程契约见 **[agent.md](agent.md)**（配置 · 用例 · skill · 文档四位一体对齐）。
- 原始事件数据 `workspace/events/**` 与 `workspace/events_ascii/**` **只读**；仅通过 `--csv` 读取，衍生产物仅写入 `workspace/runs/`。
- 不引入 vnpy；行情仅来自 zer0share / `engines/data/vendor.py`。
- Agent 调用 CLI 时使用 `--format json --quiet`，仅解析 stdout 的 JSON 信封。
- 涉及成交、退出或涨跌停语义时，补合成 panel 单测并运行相关 `pytest -q`。
- 不修改 `configs/examples/*` 作为实验；实验配置写入 `configs/experiments/`。

## Codex 入口与技能

- Codex 会读取本文件；`.codex/hooks.json` 会拦截对原始事件文件的编辑工具调用。无论 hook 覆盖范围如何，所有写入原始事件数据的 shell 命令同样禁止。
- 研究技能的权威目录是 [`.agents/skills/`](.agents/skills/)：主流程见 [`.agents/skills/qresearch/SKILL.md`](.agents/skills/qresearch/SKILL.md)，定稿闸门见 [`.agents/skills/qresearch/quality-gates.md`](.agents/skills/qresearch/quality-gates.md)。
- [`.cursor/skills/`](.cursor/skills/) 与 `.agents/skills/` 是兼容镜像。修改任一技能时必须同步另一目录，避免两种客户端的研究约定漂移。

能力演进与优先级见 [ROADMAP.md](ROADMAP.md)。
