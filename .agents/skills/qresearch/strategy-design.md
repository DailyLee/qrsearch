# Strategy design — frozen market route

Create filters/ranking from the recorded train-only factor decision, then run the fixed strategy on one role of the already evaluated frozen run.

```bash
qr pipeline research --config <yaml> --run-id <evaluated_id> --role train --format json --quiet
```

Freeze signals and execution choices before validate or holdout runs. Use `risk.max_hold_sessions >= 1` and retain `max_hold` in `risk.exit_priority`: the market adapter supplies a fixed calendar holding horizon. `exit_intent_date` is internal adapter data, not a user-provided event exit instruction; do not derive rules from CSV exit columns.

`signals.filters`, `rank_by`, and `composite` are the available strategy inputs. There is no `ingest.board` field. Market universe selection is solely `sample.universe` and must be PIT daily membership.
