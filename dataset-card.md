---
license: other
license_name: cc-by-3.0-au
license_link: https://creativecommons.org/licenses/by/3.0/au/
pretty_name: Australian Companies (ASIC Company Register)
language:
  - en
tags:
  - australia
  - companies
  - business
  - acn
  - abn
  - kyb
  - entity-resolution
  - government-data
task_categories:
  - tabular-classification
  - text-classification
size_categories:
  - 1M<n<10M
configs:
  - config_name: companies
    data_files: companies.csv
    default: true
  - config_name: former_names
    data_files: former_names.csv
---

# Australian Companies (ASIC Company Register)

Every company on the Australian Securities and Investments Commission (ASIC) register: about 4 million companies with their ACN, ABN, current name, type, status and registration dates, plus 430,000+ former company names. Refreshed automatically every week from ASIC's official extract on data.gov.au.

ASIC publishes this as a tab-separated file with one row per company *name*, DD/MM/YYYY dates and an ABN of `0` when there is none. This dataset reshapes it into one row per company with ISO dates, so it loads cleanly in pandas, Spark, DuckDB or the Hugging Face viewer.

Need live lookups instead of a weekly snapshot? [AUO](https://auo.com.au) resolves any ABN or ACN against every free government register in one API call, with KYB checks and change monitoring.

## Files

### `companies.csv`: one row per company

| Column | Description |
|---|---|
| `acn` | Australian Company Number, 9 digits with leading zeros. For type `RACN` this is the ARBN. |
| `abn` | Australian Business Number, 11 digits. Blank if the company has no ABN. |
| `name` | Current company name |
| `name_start_date` | Date the current name took effect (only when the company has been renamed) |
| `type` | Company type code (see below) |
| `class` | Liability class code |
| `sub_class` | Sub class code |
| `status` | Registration status code |
| `registration_date` | Date registered (YYYY-MM-DD) |
| `deregistration_date` | Date deregistered, if any |
| `previous_state` | State of original registration, for companies registered before national registration |
| `state_registration_number` | Number assigned by that state |

### `former_names.csv`: one row per former name

| Column | Description |
|---|---|
| `acn` | ACN of the company |
| `former_name` | A name the company previously held |

Keep `acn` and `abn` as text so the leading zeros survive:

```python
import pandas as pd

companies = pd.read_csv(
    "hf://datasets/ausapis/australian-companies/companies.csv",
    dtype={"acn": str, "abn": str, "state_registration_number": str},
    parse_dates=["registration_date", "deregistration_date", "name_start_date"],
)
```

```python
from datasets import load_dataset

companies = load_dataset("ausapis/australian-companies", "companies")
former_names = load_dataset("ausapis/australian-companies", "former_names")
```

## Codes

**Type:** `APTY` Australian proprietary company · `APUB` Australian public company · `FNOS` foreign company registered in Australia · `RACN` registered Australian body (ACN column holds its ARBN) · `CCIV` corporate collective investment vehicle

**Class:** `LMSH` limited by shares · `LMGT` limited by guarantee · `LMSG` limited by shares and guarantee · `UNLM` unlimited · `NLIA` no liability · `NONE` no Australian equivalent

**Status:** `REGD` registered · `DRGD` deregistered · `SOFF` strike-off action in progress · `EXAD` external administration (receivership or liquidation) · `NOAC` not active · `CNCL` cancelled, transferred to another law · `DISS` dissolved by special Act of Parliament

**Sub class:** `PROP` proprietary other · `PSTC` proprietary superannuation trustee · `PNPC` proprietary non-profit · `HUNT` proprietary home unit company · `EXPT` exempt proprietary · `LIST` listed public · `ULST` unlisted public · `ULSN` unlisted public non-profit · `ULSS` unlisted public superannuation trustee · `LISN` licensed to omit "Limited" · `LISS` licensed to omit "Limited", superannuation trustee · `NLTD` non-profit public without "Limited" · `RACA` / `RACO` registrable Australian corporation (association / non-association) · `PUBF` foreign company lodging a balance sheet · `STFI` small transferring financial institution · `NONE` unknown. `WHSL` also appears but is not defined in ASIC's data dictionary.

## Coverage

As described by ASIC: all currently registered companies, companies deregistered within the past year, and historical names of registered companies. It is a point-in-time snapshot of the register, published by ASIC every week; check ASIC Connect for real-time details.

## Source and licence

Source: Australian Securities and Investments Commission (ASIC), [Company Dataset](https://data.gov.au/data/dataset/asic-companies) on data.gov.au, licensed under [Creative Commons Attribution 3.0 Australia](https://creativecommons.org/licenses/by/3.0/au/).

Changes from the original: reshaped from one row per company name to one row per company, former names split into a separate file, dates converted to ISO 8601, ABN `0` replaced with blank, whitespace trimmed, and the "Modified since last report" flag dropped. No records were added or removed.

Not affiliated with or endorsed by ASIC. Built by [AUO](https://auo.com.au); the build code is at [github.com/webrating/auo-australian-companies-dataset](https://github.com/webrating/auo-australian-companies-dataset).
