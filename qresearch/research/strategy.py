"""Adapters from frozen market observations to the signal engine schema."""

from __future__ import annotations

from datetime import date

import polars as pl

from qresearch.config.models import ResearchConfig
from qresearch.research.domain import ResearchDataset


def build_market_signal_frame(
    dataset: ResearchDataset,
    config: ResearchConfig,
    calendar: list[date],
) -> pl.DataFrame:
    """Map frozen market observations to the existing signal/backtest input columns.

    Rows whose fixed holding exit lies beyond the available calendar cannot be
    simulated and are deliberately omitted rather than assigned a guessed exit.
    """
    max_hold = config.risk.max_hold_sessions
    if max_hold is None or max_hold < 1:
        raise ValueError("risk.max_hold_sessions must be at least 1 for market strategies")
    if "max_hold" not in config.risk.exit_priority:
        raise ValueError("risk.exit_priority must include max_hold for market strategies")

    sessions = sorted(set(calendar))
    session_index = {session: index for index, session in enumerate(sessions)}
    feature_columns = [column for column in dataset.frame.columns if column.startswith("features.")]
    columns = [
        "instrument",
        "decision_date",
        "entry_intent_date",
        "exit_intent_date",
        *feature_columns,
    ]
    rows: list[dict[str, object]] = []
    for observation in dataset.frame.iter_rows(named=True):
        entry = observation["effective_session"]
        if not isinstance(entry, date):
            entry = date.fromisoformat(str(entry))
        entry_index = session_index.get(entry)
        if entry_index is None:
            continue
        exit_index = entry_index + max_hold
        if exit_index >= len(sessions):
            continue
        decision = observation["asof_session"]
        if not isinstance(decision, date):
            decision = date.fromisoformat(str(decision))
        rows.append(
            {
                "instrument": observation["instrument"],
                "decision_date": decision,
                "entry_intent_date": entry,
                "exit_intent_date": sessions[exit_index],
                **{column: observation[column] for column in feature_columns},
            }
        )
    if not rows:
        schema: dict[str, pl.DataType] = {
            "instrument": pl.Utf8,
            "decision_date": pl.Date,
            "entry_intent_date": pl.Date,
            "exit_intent_date": pl.Date,
        }
        schema.update({column: dataset.frame.schema[column] for column in feature_columns})
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows).select(columns)
