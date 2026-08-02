# 因子分析（Factor analysis）

入口：[SKILL.md](SKILL.md)。本文件只负责**入场相关证据**，不定止盈止损 / 订单有效期。  
信号落地后的密度/定稿否决见 [quality-gates.md](quality-gates.md)。

## 目标

从事件特征中建立**候选因子池**，再定：一条或多条排序键、若干过滤条件、可选合成得分与符号方向；写出可检验的 `hypothesis`。

**禁止默认「只取 IC 榜前两名」**：数量由证据与冗余度决定（可以 1 个，也可以 3～5 个进入 filter/rank/composite），不是固定 2。  
**禁止**在本文或对话中给出可抄的「推荐特征+阈值」配方；阈值只来自本轮 train 证据。

## 板块（先定再比 IC）

`ingest.board` 默认 `limit10`（约 10% 涨跌停）。`limit20`=科创 688/689 + 创业 300/301。  
IC / compare **必须在单一 board 内**做；混 `all` 的结论勿直接当信号依据。切换用 YAML 或全局 `--board`。

## 年份覆盖

充分利用 events：`validate-events` / `sample_profile` 有哪些年就规划哪些年；**勿因扫描窗截断或年未过完丢弃**。较短年默认可进 train（或单独 validate）；holdout 另选。切分细节见 [backtest-optimize.md](backtest-optimize.md)。

## CLI（训练年，勿含 holdout）

```bash
qr data ping --format json --quiet
qr data validate-events --csv <events.csv> --config <base.yaml> --format json --quiet
# summary 含 board / n_limit10 / n_limit20；确认与研究假设一致后再 compare

# 1) raw IC（默认 preprocess.enabled=false）
qr factor compare --csv <train_years...> --config <base.yaml> --format json --quiet

# 2) 中性对照（必做）：在 experiments 写临时 YAML（勿改 examples），
#    仅设 factors.preprocess.enabled: true，再 compare。
# 注意：单独跑 qr factor preprocess 只落盘 __prep 列，不会自动进入下一次 compare；
#       IC 对照必须以 enabled:true 的 compare（或 research）为准。
qr factor compare --csv <train_years...> --config <yaml_preprocess_on.yaml> --format json --quiet
```

信封含 `run_id` / `artifacts`；`study decision` 时加 `--run <run_id>`（可指向 raw 或 prep run；evidence 里两个都写）。  
走 `factors` 白黑名单（默认排除 name/industry/绝对价位等）。

## 预处理（配置默认关；因子分析阶段必做对照）

引擎默认 `enabled: false`。完整因子分析 = **raw compare + enabled:true compare** 各一次。  
缺默认字段 `features.industry` / `features.total_mv`（或配置的 industry/size field）→ 可跳过，decision 写原因。

- 流水线：winsorize → industry → size → zscore → `features.X__prep`
- 只影响诊断 IC；信号/回测默认用**原始**特征名
- `__prep` 后 |IC|/excess 塌缩 → 多为行业/市值暴露，慎入池  
- 行业中性**不等于**组合分散；持仓同行上限见 strategy-design 的 `portfolio.max_*_industry*`

## 必读产物

| 来源 | 用途 |
|------|------|
| 信封 `run_id` / `artifacts.run_dir` | compare 落盘根目录；`study decision --run` |
| 信封 `summary.sample_profile` / `artifacts/sample_profile.json` | 样本年份、标的数、重复键 |
| 信封 `summary.icir_top` / `artifacts/icir_summary.csv` | 跨年稳定性 |
| `artifacts/ic_summary.csv` | 多 horizon Rank IC（绝对收益） |
| 信封 `summary.alpha_top` / `excess_ic_top`；`artifacts/alpha_beta_summary.csv` | 相对基准 alpha/beta、残差 IC |
| `artifacts/quantile_returns.csv` | 分层收益 / 超额 / 分层 alpha 是否单调 |
| 信封 `summary.corr_top_pairs` / `artifacts/factor_corr.csv` | 候选因子两两相关（冗余检查优先读此，勿手算） |
| 信封 `summary.monotonicity` | 分层 `mono_score` / `monotonic` + **`shape`**: `mono_up\|mono_down\|u\|inv_u\|hump\|weak\|n/a` |
| 信封 `summary.rejected_constant` | 近常量/缺列否决 |
| `artifacts/factor_diagnostics.json` | corr + mono/shape + constant 汇总 |
| `qr factor band-ic` 产物 | 全样本 vs 带内 IC（区间假说验证；仅 train） |
| `artifacts/preprocess_report.json` / `events_preprocessed.parquet` | 预处理步骤与 `__prep` 列（仅 enabled） |

相对基准（配置 `benchmark.instrument`，与脚手架一致）事件级指标：

- `R_stock = ols_alpha + ols_beta * R_bench + e`（同一事件池的市场暴露；特征无缺失时各因子相同）
- `rank_ic_excess`：因子对 `(R_stock - R_bench)` 的 Rank IC（偏市场中性预测力）
- `top_bottom_excess`：按因子分位的高组−低组平均超额（选主因子时优先看这个 / excess IC）
- 分层表含 `mean_excess`、分层 `ols_alpha` / `ols_beta`

## 解读步骤

1. 丢掉无效列（空列、字符串、价格绝对位）。
2. raw：按特征看 horizon 1/5/10/20，符号与 |IC| / |ICIR| 是否稳定。
3. **preprocess 对照（必做）**：对比 raw vs `__prep`；塌缩则标「暴露型」，慎入池。
4. 对照 **残差 IC / alpha**：raw IC 强但 `rank_ic_excess≈0` 且 beta≈1 → 市场暴露，剔出。
5. 建 **候选池（Top 5～8，非仅 Top 2）**：|ICIR|、|rank_ic_excess|、`top_bottom_excess`、分层形状、且中性后仍有一定残差的优先。
6. **冗余检查（必做）**：读 `corr_top_pairs`（|ρ| 高则只留代表）；不同逻辑可并存。近常量见 `rejected_constant`。
7. **分层分流（必做）**：
   - `shape` ∈ `mono_up`/`mono_down` → 单调支路：可进 `rank_by` 或单侧 filter；与符号一致。
   - `shape` ∈ `u`/`inv_u`/`hump` → **区间假说支路**（见下）；**禁止**仅因全样本 Rank IC≈0 否决。
   - `weak`：既非单调也非清晰峰谷 → 慎入池（不是自动丢弃区间因子）。
8. 角色：区间因子只做 `filters`（`op: between`）；单调因子做 `rank_by` / 单侧 filter；可 `composite`。
9. 方向（单调）：excess/raw IC > 0 → 高侧；< 0 → 低侧。
10. 入选/否决理由写进 decision（含形状支路与中性对照）。
11. **密度预估**：对拟硬过滤字段看取值频次 / 预期 `keep_frac`。近稀有二元/`rejected_constant` 慎作硬 `eq`；过稀则倾向 rank/composite，或在策略阶段标 `signal_sparse`（G1）。

## 区间因子支路（band filter）

区间因子 = **选赛道**（提高胜率/降噪声），不单独扛收益；主 alpha 仍来自带内单调因子。

1. 读 `shape`（U / 倒 U / 单峰）→ 写经济含义（区间假说用文字描述，边界用 train 上 `band-ic`/sweep 定，勿抄固定数值带），写入 `hypothesis.statement`。
2. 仅 **train** 跑条件 IC：

```bash
qr factor band-ic --csv <train...> --config <yaml> \
  --feature features.<band_feat> --lo <lo> --hi <hi> \
  [--inside-feature features.<mono_feat>] --format json --quiet
```

对照全样本 vs 带内 IC/IR；带内 n 过少（建议 ≥ max(50, 2×`gates.min_trades`)）→ 加宽带或否决。  
**禁止**用 holdout 调带后再报带内 IC。边界**勿当默认策略**。

3. YAML 落盘（语法占位；字段与阈值仅来自本阶段证据）：

```yaml
signals:
  filters:
    - { field: features.<band_feat>, op: between, value: <lo>, value_max: <hi> }
    - { field: features.<mono_feat>, op: le, value: <thr> }
  rank_by:
    - { field: features.<rank_feat>, ascending: <bool> }
```

4. 训练年搜边界（勿与 sensitivity 联乘）：

```bash
qr pipeline sweep --csv <train...> --config <yaml> \
  --set "signals.filters[field=features.<band_feat>].between=<lo:hi>,..." \
  --max-grid <N> --format json --quiet
```

看 `n_events_kept`；窄带过拟合或触发密度闸门 G1 → 否决/加宽。  
信号定稿后：`apply-best`（候选）→ research → [quality-gates.md](quality-gates.md) → 再定执行层（sensitivity）。

## 假设构造（必须）

在定策略前写清（并写入 YAML `hypothesis`）：

- `id` / `statement`：经济逻辑 + 入选因子列表（勿只写「取了最好的两个」）
- `expected_sign`：每个入选特征一条，如 `features.<name>: negative|positive`
- 样本切分：`train=... / holdout=...`（年份来自 `sample_profile`，含全部可用年规划）

反模式（抄示例、Top2、还原旧 signals 等）见 [research-loop.md](research-loop.md)。

## 因子组合（默认应考虑，非可选项）

优先按证据选结构（可组合）：

| 结构 | 何时用 |
|------|--------|
| 多键 `rank_by` | 2～3 个方向一致、冗余低的排序因子 |
| 多条 `filters` + 排序 | 若干质量/拥挤门槛 + 1～2 个排序键 |
| `signals.composite` | ≥2 个连续型因子需加权合成时（写清权重与 ascending） |

单因子仅在「候选池里只有一个过线且其余全 redundant/弱」时才可接受，并在决策存档里写明。

详见 [strategy-design.md](strategy-design.md)。

## 本阶段交付

- 候选池摘要（入选 / 否决及理由，含冗余）
- 建议的 filters / rank_by / composite（可多于 2 个因子；尚不写死 risk/execution）
- **决策存档**（必做）：

```bash
qr study decision --study <study_id> --stage factor_analysis \
  --summary "池=A,B,C,...; 结构=...; preprocess对照=done|skipped:<reason>" \
  --rationale "依据 icir/excess/分层/冗余/中性对照；非仅 Top2" \
  --evidence "{\"raw_run\":\"\",\"prep_run\":\"\",\"chosen\":[],\"rejected\":[]}" \
  --run <raw_or_prep_run_id> --next-action "strategy_design" --format json --quiet
```

- 下一动作：进入策略定制
