# 多策略组合（账户外合成）

> Agent 研投专章：分腿研究 → 账户层纸面合成。  
> **禁止**单 YAML 塞两套 risk 假装多策略。  
> 闸门 P* → [quality-gates.md](quality-gates.md)；反模式 → [research-loop.md](research-loop.md)；动机仅本文件 §0.4。

---

## 0. 问题与结论

### 0.1 用户场景（示例）

同一事件宇宙上按制度变量分流（如 `%B`）：

| 袖子 (sleeve) | 宇宙 | 逻辑 | 因子 | 止盈止损 |
|---------------|------|------|------|----------|
| A | 低侧 | 均值回归 | 套 A | risk A |
| B | 中高侧 | 趋势/突破 | 套 B | risk B |

资金在账户层按用户给定（或单一简单规则）权重合成。

### 0.2 结论（Agent 必须遵守）

1. **每个袖子 = 独立研究单元**（独立假设、YAML、train/OOS、闸门、promote）。  
2. **组合层只做权重与共享约束**，不回头用组合净值拧单腿 alpha/risk。  
3. 当前能力路径：**分腿用现有 `pipeline research` / `ops run`；组合用 Skill 纸面合成 + decision evidence**。  
4. `signals.composite` = 多因子一个排序分，**不是**多策略。

### 0.3 现有工具边界（如实使用）

| 能力 | 现状 |
|------|------|
| 单策略 signals + 单套 risk + 单账本回测 | ✅ |
| `ops run` 单 package | ✅ |
| 引擎内多 sleeve 合并净值 / 共享现金 | ❌ → 纸面合成并标 `book_paper_only` |
| 多 package 一键合并意图 | ❌ → 分腿 `ops run` 后按 §3 合并 |

### 0.4 何时值得做多策略合成（研究动机）

分两层：**是否值得开多腿研究**，以及 **是否值得定稿 portfolio book**。  
「用户口头说组合」只触发读本专章，**不等于**必须做 book。

#### 值得考虑开多腿（满足多数即可探索）

1. **互斥或制度分流**：同一事件宇宙上，不同子域需要**不同经济逻辑**，且一套 signals+risk 无法同时服务两边而不互相伤害。  
2. **执行层冲突**：两边合理的持有期 / 止盈止损 / 退出优先级明显不同；硬揉成单套 risk 会系统错配至少一腿。  
3. **宇宙可分割且样本量够**：分流后各袖子在 **train** 上仍满足密度/笔数闸门（见 quality-gates）；若一腿先天稀疏，先否决该腿，不硬凑。  
4. **用户明确要账户层分仓**（资金比例、独立 promote），而不是「再找一个更强的单策略」。  
5. **同 board**：拟合成的袖子必须同一 `ingest.board`（禁止 limit10 与 limit20 混 book）。

#### 应继续单策略（出现任一条则优先单腿）

1. 所谓「两套逻辑」只是同一因子的不同阈值，用 **一个** filter/rank + 一次 sweep 即可表达。  
2. 分歧只在执行层旋钮（stop/take/max_hold），应用 **sensitivity** 定一稿，而不是开第二 study。  
3. 分流后某一腿 IC/经济性明显不成立，或事件语义不支持 → **不做该腿**，不是硬开 book。  
4. 目标只是提高夏普：应先穷尽单策略证据与闸门，而不是用组合净值「平均掉」单腿弱点。  
5. 样本不足以支撑两套独立 OOS / 闸门 → 降级为单策略或缩小假设。

#### 何时才进入组合层（定稿 book）

须 **全部**满足，再写 book decision / 纸面合成：

| # | 条件 |
|---|------|
| 1 | 每个拟入 book 的袖子已走完单策略闭环，且过适用的单策略质量闸门（P0） |
| 2 | 各袖子假设、YAML、train/OOS run、（建议）promote 包或冻结配置已钉死 |
| 3 | 权重来源合法：用户书面给定，或单一简单规则且写入 decision（禁止 book 层网格搜权重） |
| 4 | 共享约束已声明（重复持仓、总行业/单票 cap 等；可为 null 但须明示） |
| 5 | 汇报能并列分腿指标；相关/同跌已做或显式标 `correlation_unknown` |
| 6 | 合成结果标注 `book_paper_only`（无共享现金/统一 pretrade，不得当实盘完备） |

**未满足时**：停在分腿研究或只披露分腿对比，**禁止**称为已定稿组合。

#### Agent 决策速查

```
用户要「组合 / 两套逻辑」？
  ├─ 否 → 单策略闭环
  └─ 是 → 读本专章
        ├─ 仅阈值/执行旋钮分歧 → 单策略（sweep / sensitivity）
        ├─ 真·制度分流 + 执行冲突 → 开多 sleeve study（仍各自闭环）
        │     ├─ 一腿失败 → 放弃该腿，可保持单策略 champion
        │     └─ 多腿均过闸 + 权重/约束就绪 → portfolio book（纸面合成）
        └─ 仅想刷合成夏普 → 拒绝；先单腿证据
```

---

## 1. 架构（研究单元）

```
事件宇宙（共享 CSV）
   ├─ Sleeve A：独立 study / YAML / 闸门 / promote
   └─ Sleeve B：独立 study / YAML / 闸门 / promote
            └─ Portfolio book（decision evidence + 纸面合成 + 分腿 ops 合并）
```

**铁律**

- 袖子研究 **禁止** 看另一袖子的 holdout 再改参。  
- 组合权重变更 = 新 decision，**不是**单策略 optimize 的一维。  
- 单腿可各自 promote；**book 不** promote 成「假单策略包」。组合净值仅纸面评估（`book_paper_only`）。

---

## 2. 编排剧本

### 2.1 何时启用

**触发读本文**：策略组合、多策略、分仓、sleeve、两套止盈止损、账户层合成、book / overlay 等。

**是否开多腿 / 定稿 book**：先过 §0.4；口头「要组合」≠自动进入步骤 4–6。  
澄清结论须显式其一：`stay_single` | `explore_sleeves` | `book_freeze` | `drop_sleeve`。

### 2.2 闭环（必须按序）

```
Task Progress:
- [ ] 0. 澄清：分流字段、袖子经济逻辑、资金权重、共享约束、board + §0.4 动机结论
- [ ] 0b. 若 stay_single → 退出本专章，走单策略闭环
- [ ] 1. 为每个袖子开独立 study_id + qr config new（禁止抄 examples 信号）
- [ ] 2. 袖子 A：因子→策略→train→闸门→(optimize|sweep)→sensitivity→OOS→promote?
- [ ] 3. 袖子 B：同上（可与 A 并行研究，但 decision 分离）；失败则可 drop_sleeve
- [ ] 4. 拟入 book 的袖子均过 quality-gates 后，才进入组合层（§0.4 定稿条件）
- [ ] 5. 写 portfolio book evidence（权重、共享 cap、包/配置钉死）
- [ ] 6. 组合评估：分腿指标并列 + 合成纸面（§4）；study decision
- [ ] 7. 日常：分腿 ops → 按 §3 合并 intents
```

### 2.3 命名

```
study_id:   <topic>_<sleeve>_<board>_<tag>
config:     configs/experiments/<study_id>_vN.yaml
package:    <topic>_<sleeve>_<board>==<version>
book:       study decision evidence（--stage other）；勿虚构 configs/portfolios/ 或未有 CLI
```

tag 可用日期戳区分批次；**勿**把研究起止年写进 id 暗示丢年。

### 2.4 袖子研究纪律

每个袖子完整走：`factor-analysis` → `strategy-design`（`qr config new`）→ `backtest-optimize` + `quality-gates`。  
及格线沿用用户当次声明。

**分流字段**

- 制度分流进该袖子 `signals.filters`（`between` / `le` / `ge`）。  
- **禁止**两袖子共用抄来的因子阈值；各自 train 证据。  
- 边界仅在该袖子 **train** 上用 sweep / `band-ic` 定；**禁止**用 holdout/组合期回调边界。

### 2.5 组合层 decision

```bash
qr study decision --study <book_study_id> --stage other \
  --summary "portfolio book freeze: weights + packages pinned" \
  --rationale "..." \
  --evidence '<json: sleeves, weights, shared_caps, package pins>' \
  --format json --quiet
```

Evidence 字段（示意；数值来自当次决策/用户给定）：

```json
{
  "book_id": "<book_id>",
  "board": "<limit10|limit20>",
  "sleeves": [
    {
      "id": "<sleeve_id>",
      "study_id": "...",
      "package": "<name>==<version>",
      "config": "configs/experiments/....yaml",
      "weight": "<user_or_rule>",
      "universe_rule": {"field": "features.<split>", "op": "<op>", "value": "<from_train>"},
      "train_run": "...",
      "holdout_run": "..."
    }
  ],
  "weights_sum": 1.0,
  "shared_constraints": {
    "max_gross_exposure": 1.0,
    "max_names_total": null,
    "max_names_per_industry": null,
    "max_weight_per_name": null,
    "reject_duplicate_instrument": "prefer_higher_sleeve_priority|skip_second"
  },
  "evaluation": {
    "note": "sleeve metrics from own OOS; book metrics paper-merge only",
    "bypass": ["book_paper_only"]
  }
}
```

### 2.6 闸门与反模式

- 闸门：**P0–P5** 见 [quality-gates.md](quality-gates.md)。  
- 反模式：[research-loop.md](research-loop.md)「多策略 / book」。

---

## 3. 日常 Ops（现有 CLI）

```bash
qr ops run --asof YYYYMMDD --csv <events.csv> \
  --package <sleeve_A>==<ver> --mode paper --format json --quiet
qr ops run --asof YYYYMMDD --csv <events.csv> \
  --package <sleeve_B>==<ver> --mode paper --format json --quiet
# 合并 workspace/runs/ops_*/orders_*.json（规则如下）
```

合并规则（写死）：

1. 每条 intent 打上 `sleeve_id`、`sleeve_weight`、`budget_cash = total_cash * weight`。  
2. 袖子内仍按该包 `portfolio.max_new` / `max_weight` 理解。  
3. **跨袖子同一 `instrument`**：默认 `prefer_higher_sleeve_priority`（book evidence 声明优先级）。  
4. 共享 `max_names_per_industry`：合并后再裁一次。  
5. 输出写入 `workspace/runs/`（如 `ops_book_<asof>/orders_<asof>.json`），勿改 events。

卖出：各袖子独立 state；勿假设统一会话已按袖子 risk 退出。

---

## 4. 组合评估

### 4.1 分腿（必须）

对每个 sleeve 已有 run：并列 Sharpe、ann_return、`mean_invested`、退出结构、角色窗。

### 4.2 纸面合成

- 输入：各袖子 `artifacts/equity.csv`  
- 合成：\( NAV_t = \sum_i w_i \cdot NAV^{(i)}_t / NAV^{(i)}_0 \)（或日收益加权）  
- 输出：书级 Sharpe/ann/max_dd，**必须**标 `book_paper_only`  
- 忽略共享现金争抢 / 重复票 / 共享行业 cap → 偏乐观；decision 写明假设

### 4.3 相关与同跌

- 袖子日收益相关、同为负的月份占比；未做则标 `correlation_unknown`。  
- stress：分腿 + 书级都披露；差 ≠ 机械否决。

---

## 5. 课题映射（流程示意，非参数配方）

1. 制度分流字段进各袖子 `signals.filters`（边界仅用该腿 train）。  
2. 第二腿须先过事件语义 / 因子证据；不支持则 `drop_sleeve`。  
3. 已有单策略 champion 若入 book，evidence 写明角色，禁止 silently 混逻辑。  
4. 拟入书腿均过闸 → 权重 → 纸面 merge + `book_paper_only`。  
5. stress / 覆盖异常年：书级差只归因，不机械否决。

---

## 6. 向用户汇报（定稿 book 时）

必含：分腿指标表、权重与来源、共享约束、旁路标签（至少 `book_paper_only`）、book decision 路径。  
禁止：单 YAML 双 risk；虚构 `book-run` / `book-compose`；用合成夏普回头改单腿。
