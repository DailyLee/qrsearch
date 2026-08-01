# workspace

Local working directory for inputs and run artifacts. Contents (except this file) are gitignored.

| Path | Purpose |
|------|---------|
| `events/` | Event CSVs for research / backtest |
| `runs/` | Immutable experiment runs (`qr pipeline research`) |
| `models/` | Promoted model packages (`qr promote`) |
| `cache/` | Price panel and other caches |

Defaults come from `AppSettings` in `qresearch/config/models.py`. Override via `.env` if needed.
