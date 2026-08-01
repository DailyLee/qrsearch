# 因子分析（Factor analysis）

入口：[SKILL.md](SKILL.md)。本文件只负责**入场相关证据**，不定止盈止损 / 订单有效期。

## 目标

从事件特征中建立**候选因子池**，再定：一条或多条排序键、若干过滤条件、可选合成得分与符号方向；写出可检验的 `hypothesis`。

**禁止默认「只取 IC 榜前两名」**：数量由证据与冗余度决定（可以 1 个，也可以 3～5 个进入 filter/rank/composite），不是固定 2。

## CLI

```bash
qr data ping --format json --quiet
qr data validate-events --csv <events.csv> --config <base.yaml> --format json --quiet

# 训练年（勿含 holdout）；compare 会落盘 run 并返回 run_id
qr factor compare --csv <train_years...> --config <base.yaml> --format json --quiet
# 信封含 run_id / artifacts；决策时加 --run <run_id>
qr factor ic --csv <train_years...> --config <base.yaml> --feature features.<name> --format json --quiet
```

走 `factors` 白黑名单（默认排除 name/industry/绝对价位等）。

## 预处理（解耦，默认关）

缩尾 → 行业中性 → 市值中性 → z-score 在独立模块，**不改 IC/回测内核**。开启后只影响因子诊断用的列（`features.X__prep`）；信号/回测仍用原始特征。

```bash
# 单独跑预处理并落盘
qr factor preprocess --csv <train_years...> --config <yaml> --format json --quiet

# compare / research：YAML 里 factors.preprocess.enabled: true 时自动对诊断特征用 __prep 列算 IC
```

配置要点见 `factors.preprocess`（`cross_section: all|date`，`industry_field` / `size_field`）。  
读结果时对比 raw IC vs `__prep` IC：中性后 IC 消失 → 原信号主要是行业/市值暴露。

## 必读产物

| 来源 | 用途 |
|------|------|
| 信封 `run_id` / `artifacts.run_dir` | compare 落盘根目录；`study decision --run` |
| 信封 `summary.sample_profile` / `artifacts/sample_profile.json` | 样本年份、标的数、重复键 |
| 信封 `summary.icir_top` / `artifacts/icir_summary.csv` | 跨年稳定性 |
| `artifacts/ic_summary.csv` | 多 horizon Rank IC（绝对收益） |
| 信封 `summary.alpha_top` / `excess_ic_top`；`artifacts/alpha_beta_summary.csv` | 相对基准 alpha/beta、残差 IC |
| `artifacts/quantile_returns.csv` | 分层收益 / 超额 / 分层 alpha 是否单调 |
| `artifacts/preprocess_report.json` / `events_preprocessed.parquet` | 预处理步骤与 `__prep` 列（仅 enabled） |

相对基准（配置 `benchmark.instrument`，如 `000852.SH`）事件级指标：

- `R_stock = ols_alpha + ols_beta * R_bench + e`（同一事件池的市场暴露；特征无缺失时各因子相同）
- `rank_ic_excess`：因子对 `(R_stock - R_bench)` 的 Rank IC（偏市场中性预测力）
- `top_bottom_excess`：按因子分位的高组−低组平均超额（选主因子时优先看这个 / excess IC）
- 分层表含 `mean_excess`、分层 `ols_alpha` / `ols_beta`

## 解读步骤

1. 丢掉无效列（空列、字符串、价格绝对位）。
2. 按特征看 horizon 1/5/10/20：符号是否一致、|IC| / |ICIR| 是否稳定。
3. 对照 **残差 IC / alpha**：若原始 IC 强但 `rank_ic_excess≈0` 且 beta≈1 → 多半是市场暴露，剔出候选池。
4. 建 **候选池（通常看 Top 5～8，不是只看 Top 2）**：|ICIR|、|rank_ic_excess|、`top_bottom_excess`、分层单调同时过线的特征都留下。
5. **冗余检查（必做）**：经济含义相近或同向极强相关的只留代表（例：两个都是「短窗动量」→ 留 1 个）；不同逻辑（动量 / 换手 / 波动 / 形态质量）可并存。
6. 角色分配（同一池可进多种角色，勿默认砍到 2 个）：
   - **rank_by**：1～3 个键（主排序 + 次排序打破平局）；或
   - **composite**：≥2 个有证据的成分 zscore 加权后，对合成列 `rank_by`；
   - **filters**：对仍有预测力、适合做门槛的因子设分位/阈值（可多条）。
7. IC 方向 → 排序/过滤方向：
   - IC（或 excess IC）> 0 → `rank_by.ascending: false`（或 filter 取高侧）
   - IC（或 excess IC）< 0 → `rank_by.ascending: true`（或 filter 取低侧）
8. 弱因子（|IC|≈0、分层混乱、纯 beta）→ 不进池；池内因子在交付时写清「为何入选 / 为何否决」。

## 假设构造（必须）

在定策略前写清（并写入 YAML `hypothesis`）：

- `id` / `statement`：经济逻辑 + 入选因子列表（勿只写「取了最好的两个」）
- `expected_sign`：每个入选特征一条，如 `features.pre_r1: negative`
- 样本切分：`train=YYYY-YYYY, holdout=YYYY`

**禁止**：跳过本文件直接抄示例 YAML 当「因子驱动结论」（用户只要 smoke 除外）。  
**禁止**：无冗余分析就机械取 IC 前两名定策略。

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
  --summary "池=A,B,C,...; 结构=filters+rank|composite; 否决=..." \
  --rationale "依据 icir/excess_ic/分层/冗余；非仅取 Top2" \
  --evidence "{\"icir_top\":[],\"excess_ic_top\":[],\"alpha_top\":[],\"chosen\":[],\"rejected\":[]}" \
  --next-action "strategy_design" --format json --quiet
```

- 下一动作：进入策略定制
