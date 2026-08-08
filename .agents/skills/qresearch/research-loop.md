# Research loop

1. Confirm zer0share data, PIT universe metadata, and zer0factor registry availability.
2. Materialize one named run and evaluate factors on train only.
3. Write a factor decision with evidence hashes and rationale.
4. Search signals/execution only on `role=train`, on that exact run.
5. Freeze a candidate configuration; run `train`, `validate`, and `holdout_final` separately with the same run id and role argument.
6. Compare results and record a `study decision`. Promote only after human review and full ST filtering.

Stop and correct the workflow if a command asks for CSV/events, rematerializes a new run, uses validate/holdout for search, lacks an evaluated screening manifest, or reports `st_filter_status` other than `full`.
