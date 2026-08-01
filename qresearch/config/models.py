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


class SignalsConfig(BaseModel):
    filters: list[FilterRule] = Field(default_factory=list)
    rank_by: list[RankBy] = Field(default_factory=list)


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
    commission_rate: float = 0.00034
    commission_min: float = 5.0
    stamp_duty_rate: float = 0.0005
    slippage_bps: float = 20.0


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
    mode: Literal["qfq", "hfq", "none"] = "qfq"
    as_of: str | None = None  # YYYYMMDD; None => panel end


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
    min_oos_sharpe: float | None = None
    max_oos_drawdown: float | None = None
    # None = disclose only; set e.g. 0.0 to require deflated_sharpe >= threshold
    min_deflated_sharpe: float | None = None
    max_n_trials: int | None = None
    n_trials_assumed: int = 1
    pit_strict: bool = False


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

    def data_dir(self) -> Path:
        if self.zer0share_data:
            return Path(self.zer0share_data)
        return Path(self.zer0share_root) / "data"
