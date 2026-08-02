# Plan: 多策略组合（账户外合成）

状态：`planned`  
规格源：[`.cursor/skills/qresearch/multi-strategy-portfolio.md`](../skills/qresearch/multi-strategy-portfolio.md)  
对齐：[agent.md](../../agent.md) 四位一体；ROADMAP「组合层」仍 later，本计划先做 **Skill + 薄 CLI**，引擎内多 sleeve 另开阶段。

---

## 0. 目标与非目标

### 目标

同一事件宇宙上多「袖子」(sleeve) **分腿研究 → 账户层按权重合成**；Agent 有可执行剧本；可选确定性 CLI 做 book 校验、意图合并、纸面净值合成。

### 非目标（本计划不做）

- `ResearchConfig` 内嵌多套 `risk` / 单 YAML 魔法切换 stop  
- `pipeline research` 一次跑多 sleeve  
- 组合层 Optuna / 联乘两腿网格  
- 引擎内共享现金多 sleeve 回测（→ 阶段 C，痛点证实后再开）

### 架构一句话

```
事件 CSV ─► Sleeve A study/YAML/promote ─┐
         └► Sleeve B study/YAML/promote ─┴► Portfolio book（权重+共享约束）
                                              ├ Skill：编排 / 闸门 P*
                                              └ CLI（阶段 B）：book-run / book-compose
```

---

## 1. 阶段划分

| 阶段 | 名称 | 交付 | 改代码量 |
|------|------|------|----------|
| **A** | Skill-only（P0） | 专章正式化 + 挂地图 + 闸门/反模式 | 几乎无引擎 |
| **B** | 薄 CLI（P1） | book YAML + `ops book-run` + `runs book-compose` + stage | 中等 |
| **C** | 引擎多 sleeve（P2，可选） | 共享现金回测；另开计划 | 大 |

**建议节奏**：先完整做 A；用户确认「两腿都值得合成」后再做 B；C 仅当纸面合成与真实争抢偏差会改变决策时启动。

---

## 2. 阶段 A — Skill-only（先做）

### 2.1 文档改动

| 文件 | 动作 |
|------|------|
| `multi-strategy-portfolio.md` | 状态改为正式专章；§0.4 研究动机（何时开腿/定稿/勿做）；吸收评审微调（见 §5） |
| `SKILL.md` | 文档地图增加「多策略组合」一行；触发词：组合/分仓/sleeve/book；口头≠开 book |
| `research-loop.md` | 分支：单策略 vs §0.4 多策略；Anti-patterns 并入多策略小节 |
| `quality-gates.md` | **P0–P5 权威表** + 旁路 `book_paper_only` / `correlation_unknown` / `sleeve_incomplete`；专章只链不复制 |
| `reference.md` | 阶段 A 仅注明「book 暂无 CLI，见专章」；阶段 B 再补命令 |

### 2.2 Agent 编排（写入专章，必须按序）

```
0 澄清：分流字段、袖子逻辑、权重、共享约束、board + §0.4 动机结论
   （stay_single | explore_sleeves | book_freeze | drop_sleeve）
0b stay_single → 退出多策略专章
1 每袖子 qr config new + 独立 study_id
2–3 各袖子完整单策略闭环（factor→strategy→train→搜参→sensitivity→OOS→gates）
4 拟入书袖子均过单策略闸门 + §0.4 定稿条件后才进组合层
5 写 book 清单（权重、共享 cap、package 钉死）→ study decision
6 纸面合成评估（分腿并列 + 书级指标 + 相关/同跌或 correlation_unknown）
7 日常：分腿 ops run →（Skill）合并 intents
```

### 2.3 命名（专章固化）

- `study_id`: `<topic>_<sleeve>_<board>_<tag>`（勿把起止年写进 id 暗示丢年）  
- config: `configs/experiments/<study_id>_vN.yaml`  
- book（阶段 A 可用 decision evidence JSON；阶段 B 落 `configs/portfolios/<book_id>.yaml`）

### 2.4 阶段 A 验收

1. 用户说「两套 %B 策略组合」时，Agent **不会**改单 YAML 塞两套 stop。  
2. 会开 ≥2 study，单腿闸门后再写 book decision。  
3. 汇报：分腿指标 + 权重 + 共享约束 + 旁路标签。  
4. 无 CLI 时仍能说明分腿 `ops run` + 合并规则。

### 2.5 阶段 A 明确不做

- 新增 Python 长期入口脚本当研究主路径  
- 改 `engines/backtest`  
- 写死 %B 边界 / stop / 权重配方  

---

## 3. 阶段 B — 薄 CLI（推荐第二步）

### 3.1 配置

路径：`configs/portfolios/<book_id>.yaml`（**独立于** `ResearchConfig`）

最小 schema（Pydantic 新模型，如 `PortfolioBookConfig`）：

- `book_id`, `board`, `starting_cash`  
- `sleeves[]`: `id`, `package`（优先）或冻结 `config`, `weight`, `priority`  
- `shared`: `max_names_per_industry`, `max_weight_per_name`, `on_duplicate_instrument`  
- 校验：`sum(weights)≈1`、board 一致、package/config 存在、拒写 examples  

落点建议：`qresearch/config/book.py` 或 `models.py` 旁路模块（避免污染单策略 `ResearchConfig`）。

### 3.2 命令

```bash
# 合并当日意图（循环调用现有 ops 核心）
qr ops book-run --asof YYYYMMDD --csv ... \
  --book configs/portfolios/<book>.yaml \
  --mode paper|signal \
  [--state workspace/state/<book>_state.json] \
  --format json --quiet

# 纸面合成净值（只读已有 sleeve equity）
qr runs book-compose --book configs/portfolios/<book>.yaml \
  --runs sleeveA=<run_id>,sleeveB=<run_id> \
  --format json --quiet
```

| 命令 | 行为要点 |
|------|----------|
| `ops book-run` | 每袖子跑 intents → 打 `sleeve_id`/`budget` → 去重（priority）→ 可选共享行业裁剪 → 落 `workspace/runs/ops_book_<asof>/` |
| `runs book-compose` | 读各 `equity.csv`，固定一种合成公式（见 §5），输出 `book_metrics` + `sleeve_metrics[]` + `corr` + `equity_book.csv` |

信封：`n_intents` / `by_sleeve` / `duplicates_resolved`；compose 含 `book_metrics`、`corr`。  
错误：权重非法、缺包、board 不一致 → exit **2**。

### 3.3 Decision stage

`decision_log._STAGES` 增加：`portfolio_book`（替代长期用 `other`）。  
Skill / CLI `--stage` help 同步。

### 3.4 State（signal 模式，最小）

持仓可选 `sleeve_id` + 该袖子 risk 快照字段；**阶段 B 不要求**完整共享现金会话仿真。  
文档默认：卖出仍可分腿 state，直至阶段 C。

### 3.5 单测（阶段 B 必做）

`tests/test_portfolio_book.py`（建议）：

1. 权重和 ≠ 1 → 校验失败  
2. board 不一致 → 失败  
3. 重复 instrument → `higher_priority` 保留优先级高的 sleeve  
4. `book-compose` 合成 NAV 长度对齐、权重 0.5/0.5 时中点合理  
5. 拒把 book 写进 `configs/examples/`  

跑：`pytest -q tests/test_portfolio_book.py`（及相关 decision_log）。

### 3.6 文档（阶段 B）

- `reference.md` / `README.md` CLI map  
- `multi-strategy-portfolio.md` §5 标为已实现  
- `ROADMAP.md`：组合层 → `partial`（账户外 book）  
- `agent.md`：一句「多策略用 portfolio book，非单 YAML 多 risk」

### 3.7 阶段 B 验收

- Agent 可用信封路径完成 book-run / book-compose，无需临时 py  
- 四位一体齐全  

---

## 4. 阶段 C — 引擎内多 sleeve（可选，另开计划）

**启动条件**（满足任一再开）：

- 纸面合成与「手工考虑重复票/现金」后结论相反；或  
- 实盘/paper 明确需要共享 pretrade + 袖子级 exit 同会话。

范围草案（本文件不展开实现）：

- `BookBacktestConfig` / book YAML 编排层  
- 持仓带 `sleeve_id`，退出用该袖子 risk，现金共享  
- 合成 panel 单测：冲突持仓、行业 cap  
- ROADMAP 组合层 → done/partial 更新  

---

## 5. 相对规格稿的实现约束（写入专章）

从评审结论固化，避免实现跑偏：

1. **Book 只钉 promote package**（或显式冻结 config 路径 + 指纹）；禁止指向漂移中的实验草稿。  
2. **合成公式二选一写死**（推荐：日收益加权再平衡；备选：归一 NAV 加权）；decision 必须写清假设。  
3. **权重**：用户给定或单一简单规则且落 decision；禁止 book 层网格搜权重。  
4. **同 board**：跨袖子 `ingest.board` 必须一致（对齐 G8）。  
5. **第二腿可否决**：「中高 %B / 突破」语义不支持时允许不做该腿，不硬凑 book。  
6. **文风**：禁止可粘贴 %B 边界、stop/take、权重配方、写死研究年。  
7. **旁路标签**：纸面合成必须带 `book_paper_only`，直至阶段 C。  
8. **研究动机（§0.4）**：触发读专章 ≠ 必须开 book；阈值/执行旋钮分歧走单策略；定稿 book 须 P0–P5 全过。

---

## 6. 实现顺序与工时量级（相对）

| 步 | 内容 | 依赖 |
|----|------|------|
| A1 | 专章定稿 + SKILL 地图 + research-loop + quality-gates P* | 无 |
| A2 | 用真实课题试跑一遍「双 study + book decision」（人工/Agent，不改引擎） | A1 |
| B1 | `PortfolioBookConfig` + 加载校验 | A 验收 |
| B2 | `runs book-compose` + 单测 | B1 |
| B3 | `ops book-run`（复用 ops runner）+ 单测 | B1 |
| B4 | `portfolio_book` stage + docs/ROADMAP | B2/B3 |
| C | 另开 `multi_sleeve_engine.plan.md` | B 失真证据 |

---

## 7. 风险

| 风险 | 缓解 |
|------|------|
| Agent 仍改单 YAML 双 risk | P0 闸门 + research-loop 反模式置顶 |
| 纸面过于乐观被当成可实盘 | 强制 `book_paper_only`；汇报并列分腿 |
| ops 合并卖出错误 | 阶段 B 默认分腿 state；文档不承诺统一 exit |
| book YAML 与 experiments 混淆 | 目录隔离 `configs/portfolios/`；拒 examples |

---

## 8. 完成定义（整计划）

- **A done**：专章挂载、P* 闸门、反模式、验收 §2.4 可演示。  
- **B done**：CLI 两命令 + schema + 单测 + reference/README；Agent 无需临时脚本。  
- **C**：不在本计划完成定义内。

---

## 9. 给执行者的下一动作

1. 确认只做 **A**，还是 **A+B** 一起排期。  
2. 若确认 A：按 §2.1 改 skill 文件，状态改 `in_progress` → `done`。  
3. 若确认 B：在 A done 后开 B1，勿与引擎多 sleeve 搭车。
