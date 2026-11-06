# HR dataset import

Run this after the database migration:

```powershell
cd D:\uni\Hit&Quit\hitnquit\backend
python -m alembic upgrade head
python -m scripts.seed_db --data-dir D:\data\hr --tenant-name "Example organisation"
```

`--data-dir` must contain these exact files:

- `IBM HR Employee Attrition Data.csv`
- `sample_hris_anonymized.csv`
- `HR Employee Survey Responses_Dataset.xlsx`

Only the `HR Survey Reponses` or `HR Survey Responses` worksheet is imported;
the workbook's `Data Dictionary` and other worksheets are ignored. Survey
rows are feedback-only and must contain an employee key (`EmployeeNumber`,
`employee_id`, `Employee ID`, or another documented HRIS key) matching an
employee already imported from IBM HR or HRIS. `Response ID` identifies the
feedback response and is never treated as an employee key. Rows without a
matching employee are rejected and counted. The source-to-database mapping is
implemented in `scripts/seed_db.py` and accepts the IBM names
(`JobRole`, `OverTime`, `MonthlyIncome`, `WorkLifeBalance`, `YearsAtCompany`,
`Attrition`) plus snake-case and common HRIS alternatives.

Normalization rules:

- `YearsAtCompany` is converted to `tenure_months` (`years * 12`, rounded).
- `MonthlyIncome` is stored as source currency per month; no currency conversion
  is assumed.
- `OverTime` becomes a nullable boolean and `WorkLifeBalance` a nullable
  integer.
- Empty or invalid values remain `NULL` and are rendered as `unknown` in the
  embedding text. No exit reason is inferred; `exit_reason` remains `NULL`
  when the source has none.
- The embedding text is the fixed sequence `Job title`, `Tenure`, `Overtime`,
  `Monthly income`, `Work-life balance`. Embeddings use
  `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions, normalized).

Each row receives a deterministic key from `(tenant, file name, source ID)`.
If a source has no ID, a SHA-256 hash of the row is used. A second run reports
those rows as `skipped` and does not create duplicates. The final JSON summary
contains counts plus non-sensitive `skip_reasons`, `rejection_reasons`, and
`normalization_reasons` (including `already_imported`, `missing_employee_key`,
`employee_not_found`, `missing_attrition`, and `invalid_attrition`); rejected
rows are logged with dataset and row number, never with row contents.

On subsequent runs, existing resume IDs are checked before loading the embedding
model, so embeddings are generated only for new employee rows.

The embedding model and its revision are configured through
`EMBEDDING_MODEL_PATH` and `EMBEDDING_MODEL_REVISION`. The default revision is
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; the configured model must produce
384-dimensional normalized vectors, and both its name and revision are stored
in `resumes.embedding_model`.
