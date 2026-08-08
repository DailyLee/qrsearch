# Quality gates

## Automatically enforced

- config exactly matches the frozen run;
- factor screening completed before any pipeline run;
- selected temporal role exists and has rows;
- optimize/sweep/sensitivity use only `train`;
- market promotion has frozen factor/universe lineage and `st_filter_status=full`.

## Mandatory human review before champion or promotion

1. Train/validate/final roles are temporally disjoint and final was not used in selection.
2. Candidate has sufficient observations/trades, stable results across time, and plausible economic rationale.
3. Costs, capacity, holding, stop/take assumptions, invested exposure, drawdown, turnover, and rejection reasons are reviewed.
4. Validate and final runs use the same factor refs, signal, execution, risk, and portfolio choices selected on train.
5. `st_filter_status` is full; otherwise record the limitation and do not promote.

These review items are not currently automatic statistical gates. Do not represent a successful pipeline envelope as a strategy approval.
