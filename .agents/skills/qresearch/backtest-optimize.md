# 回测与参数优化（Backtest & optimization）

入口：[SKILL.md](SKILL.md)。在信号定制之后执行；与 [strategy-design.md](strategy-design.md) 的执行/风控层衔接。  
定稿与选格否决见 [quality-gates.md](quality-gates.md)。**信封 `best_value` ≠ 可定稿。**

## 目标

评估经济表现与门禁；在**训练集**上分层搜参；OOS **只评估、不调参**；`apply-best` 只写候选，过质量闸门后方可标 champion。

## 文风

本文件只给 CLI 结构与占位符（`<train_csvs>`、`<grid_from_hypothesis>`）。  
**禁止**写死可抄的 stop/take/keep-frac 数字串或特征配方。

## 评估协议（搜参前必写）

在 YAML `evaluation:`（或至少 `hypothesis.statement`）写清：

- 样本角色：`train` / `validate`（可选，只冻结不搜参）/ `holdout`（终测 final）/ `holdout_stress`（压力）/ `full`（仅披露）
- 主指标：绝对 Sharpe 还是超额/IR（`evaluation.primary_metric` / `gates.primary_metric`）
- 用户附加目标（若有年化等）写入 `statement_hint`；选格时适用质量闸门 G5/G6
- 停手与晋升口径（多折 OOS、是否看超额等——按课题写，勿抄固定年份）

```yaml
evaluation:
  primary_metric: absolute   # or excess
  train_years: ["<y1>", "<y2>", "..."]   # 从 sample_profile.years 填写
  validate_years: ["<optional>"]
  holdouts:
    - { years: ["<final_year(s)>"], role: final, label: final_oos }
    - { years: ["<stress_year(s)>"], role: stress, label: stress_oos }
  statement_hint: "<晋升口径与多目标；年份以产物为准>"
```

引擎**不**按 `evaluation.*_years` 自动切 CSV；Agent 必须让 `--csv` ⊆ 该角色声明年（闸门 G9e）。  
切分厚度与角色见 [quality-gates.md](quality-gates.md) **G9–G9e**（相对量，无 7:3 类固定比例）。

### 充分利用 events（强制）

- 先 `validate-events`，再按 `sample_profile.years` / `years_span` 规划切分。
- **禁止**因「不是完整自然年」丢弃可用年。
- **full** 的 `--csv` 须覆盖本次研究已纳入的全部事件年。
- 省略某年进 holdout 规划须在 decision 写明理由。

## 样本切分（强制）

| 角色 | 做法 |
|------|------|
| train | 调参年；WF / optimize / sweep / sensitivity **只用**这些年；正式搜参须 `Y_train ≥ 2`（G9） |
| validate | **建议**冻结窗（不搜参）；`Y_all ≥ 4` 时默认要有，否则用户书面 `split_no_validate`（G9d） |
| holdout (final) | 终测 OOS；永不搜参；至少 **1** 个自然年且 `N_holdout ≥ N_holdout_min`（G9b/G9c） |
| holdout_stress | 压力 OOS；**差≠机械否决**；可与 final 分列，不替代 final |
| full | 冻结后全区间；**仅披露**；不可单独 promote |

### 切分相对纪律（无固定百分比）

1. **时序**：`max(train 年) < min(validate 年)`（若有）`< min(holdout final 年)`；禁止把更晚的年放进 train 调参。  
2. **厚度**：正式路径 = 厚 train（≥2 年）+ 非空 final holdout；不要求写死 70/30，但 holdout 不得「只剩几笔事件」（G9c）。  
3. **嵌套**：有 validate 时，选格/冻结看 train（及 validate 披露），**最后**才打 final holdout；禁止用 final 选参。  
4. **隔离**：引擎 WF 内有 purge；外部 holdout 年**不得**出现在任何搜参 `--csv`。样本紧张时优先保证 final holdout，再压缩 validate（须标签），而非取消 holdout。  
5. **Embargo**：引擎无独立 embargo 参数；若 `Y_all` 充裕且持有期可能跨年，优先在 train 与 holdout final 之间留空一年作缓冲（写入 evaluation），否则 decision 注明依赖 purge、接受残余重叠风险。

铁律：网格边界也不得在看过 OOS 后改。达标须同报 `mean_invested` / `empty_cash_share`。

---

## 参数优化纪律（强制）

搜参前在 `hypothesis.statement` 写清：**本轮只动哪一类旋钮、经济含义、成功/失败标准**。

| 原则 | Agent 动作 |
|------|------------|
| 先假设后网格 | 禁止无叙事全参数乱扫 |
| 分层 / 一次一类 | 信号 →（冻结）→ 执行/组合；**禁止** `sweep × sensitivity` 联乘；未冻结上一层不得搜下一层 |
| 只在 train 搜 | validate / holdout / stress 只评估 |
| 控制试次 | `n_trials_assumed` ≥ 本轮独立格点数；披露 deflated Sharpe；试次过大先降维 |
| 时序 WF | 正式搜参 train 跨 ≥2 自然年；单年网格不得标正式 |
| 稳健优于尖峰 | 看邻域稳定、成本加压；**`best_value` 只是候选** |
| 经济可交易 | apply-best 后过 [quality-gates.md](quality-gates.md) 才可冻结 |
| 复杂度预算 | 自由参数过多 → 先固定一类为模板 |
| 先粗后细 | 同类旋钮可两轮加密，不得顺带开新参数类；累计计入 trials |
| 成本摩擦 | sensitivity **必须**含 cost 乘数维（G7） |
| 目标一致 | 多目标时选格表并列主指标与附加目标 + invested（G6） |
| 可审计 | `study decision` 写网格维度、N、入选理由、**否决的更高夏普格** |

### 命令映射

| 参数类 | 命令 |
|--------|------|
| 单特征分位门槛 | `qr pipeline optimize` |
| 多过滤 / 区间带 | `qr pipeline sweep` |
| stop / take / hold / 仓位配额等 | `qr pipeline sensitivity` |
| 写回 | `apply-best` → research → 闸门验收 |

只用上表已有命令；勿声称做了引擎未提供的检验。反模式见 [research-loop.md](research-loop.md)。

---

## A. 回测 research

```bash
qr pipeline research --csv <train_csvs...> --config configs/experiments/<file>.yaml \
  --n-trials-assumed <N> --format json --quiet

qr analyze trades --run <run_id> --format json --quiet
qr analyze report --run <run_id> --format json --quiet
```

记下：`run_id`、metrics、gates、`pit_status`、**退出结构与 invested**。  
候选 YAML 的 train research 之后、宣布冻结之前：必跑 `analyze trades` 并过 [quality-gates.md](quality-gates.md)。

### 门禁语义（引擎）

| 字段 | 含义 |
|------|------|
| `structural_passed` / `passed` | 笔数、OOS folds 等 |
| `economic_passed` | 按 `gates.primary_metric` |
| `absolute_ok` / `excess_ok` | 双轨披露 |
| `promotable` | 结构 ∧ 经济（默认）→ 才可考虑 promote |

引擎 `promotable=true` 仍须通过 skill 质量闸门才可称完整策略。全样本 run 永不单独 promote。

### 分析清单

- [ ] `config.snapshot.yaml` 是否为本轮 YAML  
- [ ] sharpe / ann_return / max_dd / n_trades  
- [ ] `mean_invested` / `empty_cash_share`  
- [ ] 相对基准（excess / IR）  
- [ ] deflated_sharpe、`n_trials`  
- [ ] PIT  
- [ ] **质量闸门**（G0b–G6 适用项）  
- [ ] 拒单 Top  

---

## B. 执行/风控敏感度

顺序：§A 基线 + 闸门 →（默认）§C 信号搜参 → 候选验收 → §B。  
因子 IC **不能**直接给出 stop/take/validity。

```bash
qr pipeline sensitivity --csv <train_csvs...> --config <signal_frozen.yaml> \
  --cost-mult <cost_grid> \
  --stop <stop_grid> \
  --take <take_grid> \
  --max-hold <hold_grid> \
  --max-weight <weight_grid> \
  --max-new <new_grid> \
  --sizing-base <bases> \
  --max-grid <N> \
  --format json --quiet
```

格点数值来自本轮假设与上一轮诊断（**勿从 skill 抄数字**）。必须含 cost 维。

### 选格（多准则）

1. 从网格取候选（非唯一看夏普；可点名次优稳健格）。  
2. `apply-best` 写出候选 YAML（尚未定稿）。  
3. train research + `analyze trades`。  
4. 过 [quality-gates.md](quality-gates.md) G1–G7（含 G7 cost）。  
5. 通过 → decision 冻结；失败 → 废弃该候选，换格或回退。  

**敏感度 run 本身不 promote。** 未跑 §B 须声明 `execution_template`。

---

## C. 信号阈值（optimize / sweep）

**optimize/sweep = 信号侧**；**sensitivity = 执行/组合侧**。  
默认在 §B 之前。未冻结信号层证据不得开始 §B（除非用户只要执行模板实验并声明）。

### C1. `pipeline optimize`

```bash
qr pipeline optimize --csv <train_csvs...> --config <exp.yaml> \
  --feature features.<from_evidence> --side auto|high|low \
  --keep-frac <frac_grid_from_hypothesis> --format json --quiet
```

方向来自 `expected_sign` / 因子证据。写回后验收时查密度 G1 等。

### C2. `pipeline sweep`

```bash
qr pipeline sweep --csv <train_csvs...> --config <exp.yaml> \
  --set "signals.filters[field=features.<from_evidence>].value=<v_grid>" \
  --set "signals.filters[field=features.<from_evidence>].between=<lo:hi>,..." \
  --metric <primary_or_declared> --max-grid <N> --format json --quiet
```

行含 `n_events_kept`（窄带过拟合信号）。区间因子勿用 `optimize --keep-frac`。

### 写回（候选 → 验收）

```bash
qr config apply-best --from-run <opt_or_sweep_id> \
  --out configs/experiments/<name>_vN.yaml --format json --quiet
# 然后 train research + analyze trades + quality-gates；失败则不得冻结
```

可跳过信号搜参（须 decision）：用户明确不做；阈值已由证据定死；本轮只改执行层。

规则：仅 train；≥2 自然年才正式；一次一类旋钮；`n_trials_assumed` ≥ N。

---

## D. Validate / Holdout / Stress（冻结后只评估）

仅 **质量闸门通过后的冻结 YAML** 进入本段。

```bash
qr pipeline research --csv <validate_or_oos_csvs...> --config <frozen.yaml> \
  --n-trials-assumed <N> --format json --quiet
qr study decision --study <id> --stage holdout \
  --summary "<validate|holdout|holdout_stress>: ..." --rationale "..." \
  --run <run_id> --config <frozen.yaml> --format json --quiet
```

CLI stage 仅有 `holdout`（无 `backtest_validate` / `holdout_stress` 枚举）；**角色写进 summary**。  
Holdout decision：绝对/相对是否达标、`mean_invested`、角色 final vs stress。  
stress 差 → 归因，不改参；新假设开新 study。

## E. 对比与迭代

```bash
qr runs compare --runs <id1>,<id2> --format json --quiet
```

| 观察 | 动作 |
|------|------|
| 信号与 IC 矛盾 | 回因子 / 改 signals |
| 闸门 G1 密度失败 | 放宽硬过滤或改 rank；或标 sparse 旁路 |
| 闸门 G2/G3 退出畸形 | 重跑 sensitivity，否决纸面 TP |
| 结构过、经济不过 | 降换手或改执行；禁止 promote |
| 连续 2–3 轮无改进 | 停止并对比 |

默认最多 **3** 轮「改策略/优化→回测」；更多需用户同意。

## F. 全样本终测（冻结后必做，仅披露）

```bash
qr pipeline research --csv <all_year_csvs...> --config <frozen.yaml> \
  --n-trials-assumed <N> --format json --quiet
qr study decision --study <study_id> --stage full_sample \
  --summary "全样本仅披露; ..." --rationale "参数已冻结" \
  --run <run_id> --config <frozen.yaml> --format json --quiet
qr analyze report --run <full_run_id> --format json --quiet
```

## G. 晋升（可选，用户明确要求）

```bash
qr validate rolling --csv <train_csvs...> --config <final.yaml> --format json --quiet
qr promote --run <run_id> --model-id <id> --version <ver> --format json --quiet
```

仅引擎 `promotable=true` **且** skill 质量闸门通过 **且** 用户要求；`--force` 需用户明确。

## 试次（deflated Sharpe）

- 第 1 轮 research：`n_trials_assumed=1`（或配置值）  
- 每多一轮改 YAML 再 research：+1  
- 网格搜参后：后续 research 用 ≥ 该网格独立点数（累计多轮则取和或上界并写 decision）

## 本阶段交付

- `evaluation` 协议 + 各窗 `run_id`  
- 关键指标 + invested + 退出结构摘要 + 闸门结果  
- 执行层：定稿 / `execution_template` / 旁路标签  
- 搜参 decision（N、入选、否决的更高夏普格）  
- 下一动作或停手理由  
