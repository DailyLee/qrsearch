# qresearch Skill Remediation Design

## Goal

Make the market research workflow executable without sample leakage: every strategy command consumes one existing frozen run, uses an explicit temporal role, carries universe-quality lineage, and documents only active CLI behavior.

## Run and role contract

`pipeline research`, `pipeline optimize`, `pipeline sweep`, and `pipeline sensitivity` accept a required `--run-id` and an explicit `--role`. They load the frozen config and dataset from that run, reject a mismatched supplied config, then filter `dataset.parquet` by `role` before creating signals. `research` permits `train`, `validate`, `final`, and `stress`; search commands permit only `train`. Each envelope and run metadata records the role and input row count.

This intentionally removes implicit materialization from pipeline commands. Materialization and factor evaluation remain separate, deterministic CLI stages, allowing strategy output to share the exact feature snapshot and screening lineage.

## Universe-quality contract

Market materialization reads zer0share's daily universe build metadata for the requested sessions. It records the aggregate `st_filter_status` in the sample manifest and run metadata. A run with anything other than `full` is not promotable, including when `--force` is supplied; the report/provenance has enough information to explain why.

## Gate contract

The automated gate is deliberately small and objective: strategy runs must have a frozen evaluated lineage, a non-empty selected role, and full ST filtering before promotion. Statistical acceptance gates remain a documented human review checklist until their thresholds and split-comparison evidence are implemented in the engine.

## Documentation contract

The qresearch Skill, its reference, factor-analysis, strategy, backtest, quality-gate, research-loop, and multi-strategy pages are consolidated around the market-only CLI. Retired `--csv`, event, `ops`, `validate-events`, `ingest.board`, and band-IC instructions are removed. README shows the required materialize → evaluate → role-specific pipeline sequence.

## Tests

Tests prove a pipeline run reuses frozen data, filters by role, rejects non-train search, rejects config mismatch, records role evidence, reads ST status, and refuses to promote a non-full universe even with force. CLI tests ensure each pipeline subcommand exposes `--run-id` and `--role` without CSV flags.
