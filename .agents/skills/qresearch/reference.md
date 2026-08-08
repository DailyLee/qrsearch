# qresearch active CLI reference

## Deterministic research path

```text
qr data ping → qr research factors → qr config new
→ qr research materialize --run-id <id>
→ qr research evaluate --run-id <id>
→ factor_analysis decision (train evidence only)
→ qr pipeline <command> --run-id <id> --role <role>
```

Every agent invocation includes `--format json --quiet`.

```bash
qr research materialize --config <yaml> --run-id <id>
qr research evaluate --config <yaml> --run-id <id>
qr pipeline research --config <yaml> --run-id <id> --role train
qr pipeline research --config <yaml> --run-id <id> --role validate
qr pipeline research --config <yaml> --run-id <id> --role holdout_final
qr pipeline optimize --config <yaml> --run-id <id> --role train
qr pipeline sweep --config <yaml> --run-id <id> --role train --set '<key>=<values>'
qr pipeline sensitivity --config <yaml> --run-id <id> --role train
```

`research`, `evaluate`, and every pipeline command compare the supplied YAML with the run's `config.snapshot.yaml`. A mismatch, missing run, missing screening manifest, invalid role, or empty selected role is an error.

## Roles

- `train`: factor selection and all threshold/sensitivity searches.
- `validate`: strategy confirmation after parameters are frozen.
- `holdout_final`: final untouched out-of-sample assessment.
- `holdout_stress`: separately disclosed stress assessment.

The role names are values stored in `dataset.parquet`; do not use `final`, `stress`, `full`, or CSV year slicing as aliases.

## Artifacts and lineage

Materialization creates `sample_set.parquet`, `feature_snapshot.parquet`, `feature_manifest.json`, `label_set.parquet`, `dataset.parquet`, and `split_summary.json`. Evaluation adds `factor_screening_manifest.json`, `factor_redundancy.parquet`, and zer0factor evaluation evidence.

Pipeline output is written into that same run and records `pipeline_role` and `pipeline_input_rows` in `meta.json`. Sample lineage records `st_filter_status` and its daily values. Only `full` ST filtering is eligible for `qr promote`, even with `--force`.

## Removed surface

Do not call `--csv`, event ingest, `validate-events`, `qr ops`, `qr validate`, `qr factor band-ic`, or local factor preprocessing/IC commands. They are not part of qresearch.
