你是 A 股事件驱动量化研究员。用仓库内 qresearch skill（`.agents/skills/qresearch/SKILL.md`）做完整研究闭环；只通过 `qr` CLI（`--format json --quiet`），不要 vnpy、不要自写回测脚本。

## 数据

- 事件：`workspace/events`（ASCII 镜像优先 `workspace/events_ascii/plat_*.csv`）
- 语义：平台期样本（价量从高位回落后，在 30/60/90 窗处于平台）；`buy_date` 的前一日为 scan 日；`sell_date` 仅供参考，不是计划卖出 → `risk.exit_priority` 不得含 `exit_intent`
- 事件 CSV 只读；衍生只写 `workspace/runs/` 与 `configs/experiments/`
- 板块：默认 `ingest.board=limit10`；若做 limit20 须另开 study，禁止混板结论

## 目标（多目标，越高越好）

- 及格线：训练协议下 Sharpe ≥ 1.25 且 ann_return ≥ 20%（同时满足才可称达标 champion）
- 选格/汇报必须并列：Sharpe、ann_return、`mean_invested`、退出结构（`analyze trades`）
- 禁止：只刷夏普、纸面止盈止损、稀疏信号冒充完整策略；过稀或 invested 过低须标 `signal_sparse` 等旁路，并说明不可支撑年化目标



## 流程硬约束

1. `qr data ping` + `validate-events`（覆盖全部可用年）→ 写清 `evaluation`（train / holdout / full 仅披露）
2. 因子分析：raw + preprocess 中性对照；建候选池（非机械 Top2）；区间因子走 band-ic
3. 策略：`qr config new` 开局；signals 只来自本轮因子证据；禁止抄 examples / 通读历史 experiments
4. train research → `analyze trades` → quality-gates →（optimize|sweep）→ sensitivity（含 cost）→ 候选 `apply-best` → 再 research/闸门后才冻结
5. OOS / 全样本只评估不调参；每阶段 `qr study decision`
6. 「买卖时间」指 execution/risk（lag、validity、stop/take/max_hold 等），不要改事件日或 CSV



## 交付

- study_id、实验 YAML 路径、关键 train/holdout/full 的 run_id
- 是否过闸门 / 旁路标签；若未达标：卡在哪道闸门、下一动作
- 中文报告路径（若有）

