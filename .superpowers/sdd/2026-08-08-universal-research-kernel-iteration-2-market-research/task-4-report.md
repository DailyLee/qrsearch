# Task 4 Report — Fixed-Horizon Labels and Temporal Dataset

## Delivered

- Added `qresearch.research.labels` with the in-memory event-schema compatibility projection,
  fixed-horizon PIT-qfq labels, retained missing labels, and deterministic label provenance.
- Added `qresearch.research.dataset` with complete-observation-key left joins, duplicate/extra-key
  rejection, feature coverage, label-status counts, and input-hash metadata.
- Added market-label and dataset tests, including open/close horizons, split-period PIT adjustment,
  missing entry/exit bars, calendar overflow, and one-to-one join failures.
- Modernized the PIT audit test fixture to construct the strict market `ResearchConfig`; no legacy
  defaults or aliases were restored.

## TDD evidence

The initial label/dataset test run was RED because both new modules were absent. A later RED run
confirmed the compatibility-projection test fails when its public function is absent.

## Verification

`python -m pytest tests/test_market_labels.py tests/test_research_dataset.py tests/test_pit_qfq.py -q`

Result: `16 passed`.

## Notes

The compatibility projection advances its legacy `exit_intent_date` by twice the combined entry lag
and label horizon in calendar days, plus a 14-day holiday margin. This is a conservative loading
bound only; actual entry/exit dates and returns are always resolved by `PricePanel` trading
sessions, with both prices qfq-adjusted as-of the exact exit session. No event CSV or source
repository data was written.
