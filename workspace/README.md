# workspace

Local working directory for inputs and run artifacts. Contents (except this file) are gitignored.

| Path | Purpose |
|------|---------|
| `events/` | Event CSVs for research / backtest |
| `runs/` | Immutable experiment runs (`YYYYMMDD_HHMMSS_mmm`, local time) |
| `studies/` | Study spine: decisions + RUNS.md links to runs/reports |
| `runs/<id>/decisions/` | Mirrored decisions for that run (via `--run`) |
| `models/` | Promoted model packages (`qr promote`) |
| `cache/` | Price panel and other caches |

Defaults come from `AppSettings` in `qresearch/config/models.py`. Override via `.env` if needed.
