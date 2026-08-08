# Backtest and optimization discipline

All commands require an evaluated frozen `--run-id`. There is no CSV input.

```bash
qr pipeline optimize --config <yaml> --run-id <id> --role train --feature features.<name> --format json --quiet
qr pipeline sweep --config <yaml> --run-id <id> --role train --set '<key>=<values>' --format json --quiet
qr pipeline sensitivity --config <yaml> --run-id <id> --role train --format json --quiet
```

Search commands reject any role other than `train`. Once a candidate is written to a new experiment YAML, run it with `pipeline research` on `train`, then `validate`, then `holdout_final` without changing factor choice or parameters between those runs.

The engine models daily pricing, constraints, transaction costs, and configured stop/take/max-hold rules. It does not turn intraday high/low threshold touches into executable certainty beyond its documented daily-bar proxy; disclose this limitation when interpreting stop/take results.
