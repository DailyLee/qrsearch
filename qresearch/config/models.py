"""Pydantic domain config for qresearch."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _StrictConfigModel(BaseModel):
    """Configuration models reject unknown fields instead of masking drift."""

    model_config = ConfigDict(extra="forbid")


class FilterRule(_StrictConfigModel):
    field: str
    op: Literal["ge", "gt", "le", "lt", "eq", "ne", "between"]
    value: Any = None
    value_max: Any = None


class RankBy(_StrictConfigModel):
    field: str
    ascending: bool = True


class CompositeComponent(_StrictConfigModel):
    field: str
    weight: float = 1.0
    ascending: bool = True  # True => contribute -zscore (prefer lower raw values)


class CompositeConfig(_StrictConfigModel):
    enabled: bool = False
    name: str = "composite_score"
    components: list[CompositeComponent] = Field(default_factory=list)


class SignalsConfig(_StrictConfigModel):
    filters: list[FilterRule] = Field(default_factory=list)
    rank_by: list[RankBy] = Field(default_factory=list)
    composite: CompositeConfig = Field(default_factory=CompositeConfig)


class SampleConfig(_StrictConfigModel):
    universe: str
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _validate_bounds(self) -> SampleConfig:
        if not self.universe.strip():
            raise ValueError("universe must not be empty")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class FeatureRefConfig(_StrictConfigModel):
    name: str
    availability_lag_sessions: int = Field(ge=0)


class FeatureSourceConfig(_StrictConfigModel):
    provider: Literal["zer0factor"] = "zer0factor"
    refs: list[FeatureRefConfig] = Field(default_factory=list)
    analysis_family: str | None = None

    @model_validator(mode="after")
    def _validate_refs_and_family(self) -> FeatureSourceConfig:
        if not self.refs:
            raise ValueError("features.refs must not be empty")
        if self.analysis_family is None:
            return self

        try:
            from zer0factor.eval.analysis import EvaluationAnalysisRunner
            from zer0factor.factor_registry import get_family
        except ImportError as exc:
            raise ValueError(
                "analysis_family requires the zer0factor EvaluationService dependency"
            ) from exc

        accepted = EvaluationAnalysisRunner().configs
        if self.analysis_family not in accepted:
            known = ", ".join(sorted(accepted))
            raise ValueError(
                f"analysis_family {self.analysis_family!r} is not accepted by "
                f"zer0factor EvaluationService; known families: {known}"
            )
        try:
            family = get_family(self.analysis_family)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown analysis_family: {self.analysis_family}") from exc
        for ref in self.refs:
            try:
                family.analysis_dimensions(ref.name)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"feature ref {ref.name!r} cannot be parsed by analysis family "
                    f"{self.analysis_family!r}"
                ) from exc
        return self


class LabelConfig(_StrictConfigModel):
    entry_price: Literal["open", "close"] = "open"
    entry_lag_sessions: int = Field(default=1, ge=1)
    horizon_sessions: int = Field(default=5, ge=1)
    exit_price: Literal["open", "close"] = "open"


class HypothesisConfig(_StrictConfigModel):
    id: str | None = None
    statement: str | None = None
    expected_sign: dict[str, str] = Field(default_factory=dict)
    parent_run: str | None = None
    study_id: str | None = None  # links run report ↔ workspace/studies/<id>


class PortfolioConfig(_StrictConfigModel):
    starting_cash: float = 1_000_000.0
    currency: str = "CNY"
    sizing: Literal["equal_weight"] = "equal_weight"
    sizing_base: Literal["cash", "nav"] = "cash"
    max_weight: float = 0.35
    max_names: int | None = None
    max_new_entries_per_day: int = 10
    lot_size: int = 100
    # Industry diversification at pretrade (null = off)
    industry_field: str = "features.industry"
    max_names_per_industry: int | None = None
    max_new_per_industry_per_day: int | None = None


class EntryFilterConfig(_StrictConfigModel):
    enabled: bool = False
    min_open_ret: float | None = None
    max_open_ret: float | None = None
    ref: Literal["decision_prior_close", "session_prior_close"] = "decision_prior_close"


class ExecutionConfig(_StrictConfigModel):
    price: Literal["open", "close"] = "open"
    lag_sessions: int = 0
    order_validity_sessions: int = 5
    entry_filter: EntryFilterConfig = Field(default_factory=EntryFilterConfig)


class CostsConfig(_StrictConfigModel):
    # A-share style defaults: 佣金万0.8 / 最低0；印花税万5（卖出）；过户等税费万0.1；滑点10bp
    commission_rate: float = 0.00008
    commission_min: float = 0.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001  # 税费/过户费，买卖双边
    slippage_bps: float = 10.0


class RiskConfig(_StrictConfigModel):
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


class AdjustmentConfig(_StrictConfigModel):
    # qfq = PIT per-session forward adj (base = adj_factor on asof session; no window-end peek)
    mode: Literal["qfq", "hfq", "none"] = "qfq"
    as_of: str | None = None  # unused for qfq PIT; retained for cache/debug labels only


class BenchmarkConfig(_StrictConfigModel):
    instrument: str = "000852.SH"


class WalkForwardConfig(_StrictConfigModel):
    mode: Literal["expanding", "rolling"] = "expanding"
    fold_freq: Literal["year"] = "year"
    rolling_is_years: int = 2
    embargo_sessions: int = 0
    min_trades: int = 1
    objective: Literal["trade_weighted_sharpe", "mean_sharpe"] = "trade_weighted_sharpe"


class GatesConfig(_StrictConfigModel):
    min_oos_folds: int = 2
    min_trades: int = 10
    # Economic defaults: prevent "structure-only pass" looking promotable
    min_oos_sharpe: float | None = 0.0
    max_oos_drawdown: float | None = 0.35
    # None = disclose only; set e.g. 0.0 to require deflated_sharpe >= threshold
    min_deflated_sharpe: float | None = None
    # absolute (default) vs excess: which thresholds drive economic_passed
    primary_metric: Literal["absolute", "excess"] = "absolute"
    min_ann_excess: float | None = None
    min_information_ratio: float | None = None
    max_n_trials: int | None = None
    n_trials_assumed: int = 1
    pit_strict: bool = False
    require_economic_for_promote: bool = True


class HoldoutWindow(_StrictConfigModel):
    years: list[str] = Field(default_factory=list)
    role: Literal["final", "stress"] = "final"
    label: str | None = None


class EvaluationConfig(_StrictConfigModel):
    """Sample-split protocol (declaration). Engine does not auto-slice CSVs."""

    primary_metric: Literal["absolute", "excess"] = "absolute"
    train_years: list[str] = Field(default_factory=list)
    validate_years: list[str] = Field(default_factory=list)
    holdouts: list[HoldoutWindow] = Field(default_factory=list)
    statement_hint: str | None = None


class ResearchConfig(_StrictConfigModel):
    sample: SampleConfig
    features: FeatureSourceConfig
    label: LabelConfig = Field(default_factory=LabelConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    adjustment: AdjustmentConfig = Field(default_factory=AdjustmentConfig)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    hypothesis: HypothesisConfig = Field(default_factory=HypothesisConfig)
    lookback_sessions: int = 5
    delay_buffer_sessions: int = 10
    suspend_buffer_sessions: int = 5
    ic_horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20])

    @model_validator(mode="after")
    def _sync_primary_metric(self) -> ResearchConfig:
        # When evaluation declares primary or sample windows, it wins over gates.
        ev = self.evaluation
        if (
            ev.primary_metric != "absolute"
            or ev.train_years
            or ev.validate_years
            or ev.holdouts
            or ev.statement_hint
        ):
            self.gates.primary_metric = ev.primary_metric
        return self


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    zer0share_root: str = r"C:\Users\dl271\Downloads\code\zer0share"
    zer0share_data: str | None = None
    zer0factor_root: str = r"C:\Users\dl271\Downloads\code\zer0factor"
    zer0factor_factor_dir: Path = Path("../zer0factor/data/factors")
    zer0factor_db_path: Path = Path("../zer0factor/db/factor_meta.duckdb")
    qresearch_events: str = "workspace/events"
    runs_dir: Path = Path("workspace/runs")
    packages_dir: Path = Path("workspace/models")  # promoted model packages
    cache_dir: Path = Path("workspace/cache")
    studies_dir: Path = Path("workspace/studies")  # decision logs / study index

    def data_dir(self) -> Path:
        if self.zer0share_data:
            return Path(self.zer0share_data)
        return Path(self.zer0share_root) / "data"
