"""Pydantic domain config for qresearch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ColumnAliases(BaseModel):
    instrument: str = "code"
    decision_date: str | None = None
    entry_intent_date: str = "buy_date"
    exit_intent_date: str = "sell_date"
    features: dict[str, str] = Field(default_factory=dict)


class IngestConfig(BaseModel):
    aliases: ColumnAliases = Field(default_factory=ColumnAliases)
    coalesce: Literal["last", "max", "first"] = "last"
    date_formats: list[str] = Field(
        default_factory=lambda: ["%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]
    )


class FilterRule(BaseModel):
    field: str
    op: Literal["ge", "gt", "le", "lt", "eq", "ne", "between"]
    value: Any = None
    value_max: Any = None


class RankBy(BaseModel):
    field: str
    ascending: bool = True


class CompositeComponent(BaseModel):
    field: str
    weight: float = 1.0
    ascending: bool = True  # True => contribute -zscore (prefer lower raw values)


class CompositeConfig(BaseModel):
    enabled: bool = False
    name: str = "composite_score"
    components: list[CompositeComponent] = Field(default_factory=list)


class SignalsConfig(BaseModel):
    filters: list[FilterRule] = Field(default_factory=list)
    rank_by: list[RankBy] = Field(default_factory=list)
    composite: CompositeConfig = Field(default_factory=CompositeConfig)


class FactorPreprocessConfig(BaseModel):
    """Standalone factor prep (winsorize / industry / size / zscore). Default off."""

    enabled: bool = False
    steps: list[Literal["winsorize", "industry_neutral", "size_neutral", "zscore"]] = Field(
        default_factory=lambda: ["winsorize", "industry_neutral", "size_neutral", "zscore"]
    )
    winsorize_q: float = 0.01
    industry_field: str = "features.industry"
    size_field: str = "features.total_mv"
    cross_section: Literal["all", "date"] = "all"
    min_group_size: int = 30
    suffix: str = "__prep"


class FactorsConfig(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(
        default_factory=lambda: [
            "features.name",
            "features.industry",
            "features.support_levels",
            "features.resistance_levels",
            "features.outperform_index",
            "features.stock_return",
        ]
    )
    min_non_null: int = 100
    n_quantiles: int = 5
    icir_min_periods: int = 4
    max_features: int | None = 32
    quantile_horizon: int = 5
    preprocess: FactorPreprocessConfig = Field(default_factory=FactorPreprocessConfig)


class HypothesisConfig(BaseModel):
    id: str | None = None
    statement: str | None = None
    expected_sign: dict[str, str] = Field(default_factory=dict)
    parent_run: str | None = None
    study_id: str | None = None  # links run report ↔ workspace/studies/<id>


class PortfolioConfig(BaseModel):
    starting_cash: float = 1_000_000.0
    currency: str = "CNY"
    sizing: Literal["equal_weight"] = "equal_weight"
    sizing_base: Literal["cash", "nav"] = "cash"
    max_weight: float = 0.35
    max_names: int | None = None
    max_new_entries_per_day: int = 10
    lot_size: int = 100


class EntryFilterConfig(BaseModel):
    enabled: bool = False
    min_open_ret: float | None = None
    max_open_ret: float | None = None
    ref: Literal["decision_prior_close", "session_prior_close"] = "decision_prior_close"


class ExecutionConfig(BaseModel):
    price: Literal["open", "close"] = "open"
    lag_sessions: int = 0
    order_validity_sessions: int = 5
    entry_filter: EntryFilterConfig = Field(default_factory=EntryFilterConfig)


class CostsConfig(BaseModel):
    # A-share style defaults: 佣金万0.8 / 最低0；印花税万5（卖出）；过户等税费万0.1；滑点10bp
    commission_rate: float = 0.00008
    commission_min: float = 0.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001  # 税费/过户费，买卖双边
    slippage_bps: float = 10.0


class RiskConfig(BaseModel):
    stop_loss: float | None = -0.086
    take_profit: float | None = 0.158
    max_hold_sessions: int | None = None
    exit_priority: list[str] = Field(
        default_factory=lambda: [
            "stop",
            "take_profit",
            "max_hold",
            "exit_intent",
            "deferred_exit",
        ]
    )


class AdjustmentConfig(BaseModel):
    # qfq = PIT per-session forward adj (base = adj_factor on asof session; no window-end peek)
    mode: Literal["qfq", "hfq", "none"] = "qfq"
    as_of: str | None = None  # unused for qfq PIT; retained for cache/debug labels only


class BenchmarkConfig(BaseModel):
    instrument: str = "000852.SH"


class WalkForwardConfig(BaseModel):
    mode: Literal["expanding", "rolling"] = "expanding"
    fold_freq: Literal["year"] = "year"
    rolling_is_years: int = 2
    embargo_sessions: int = 0
    min_trades: int = 1
    objective: Literal["trade_weighted_sharpe", "mean_sharpe"] = "trade_weighted_sharpe"


class GatesConfig(BaseModel):
    min_oos_folds: int = 2
    min_trades: int = 10
    # Economic defaults: prevent "structure-only pass" looking promotable
    min_oos_sharpe: float | None = 0.0
    max_oos_drawdown: float | None = 0.35
    # None = disclose only; set e.g. 0.0 to require deflated_sharpe >= threshold
    min_deflated_sharpe: float | None = None
    max_n_trials: int | None = None
    n_trials_assumed: int = 1
    pit_strict: bool = False
    require_economic_for_promote: bool = True


class ResearchConfig(BaseModel):
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    adjustment: AdjustmentConfig = Field(default_factory=AdjustmentConfig)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    factors: FactorsConfig = Field(default_factory=FactorsConfig)
    hypothesis: HypothesisConfig = Field(default_factory=HypothesisConfig)
    lookback_sessions: int = 5
    delay_buffer_sessions: int = 10
    suspend_buffer_sessions: int = 5
    ic_horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20])


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    zer0share_root: str = r"C:\Users\dl271\Downloads\code\zer0share"
    zer0share_data: str | None = None
    qresearch_events: str = "workspace/events"
    runs_dir: Path = Path("workspace/runs")
    packages_dir: Path = Path("workspace/models")  # promoted model packages
    cache_dir: Path = Path("workspace/cache")
    studies_dir: Path = Path("workspace/studies")  # decision logs / study index

    def data_dir(self) -> Path:
        if self.zer0share_data:
            return Path(self.zer0share_data)
        return Path(self.zer0share_root) / "data"
