# Australian Companies Dataset (ASIC Company Register)

A clean, weekly-refreshed copy of every company on the Australian Securities and Investments Commission (ASIC) register: about 4 million companies with ACN, ABN, current name, type, status and registration dates, plus 430,000+ former names.

- **Hugging Face:** [ausapis/australian-companies](https://huggingface.co/datasets/ausapis/australian-companies)
- **Kaggle:** [ausapis/australian-companies](https://www.kaggle.com/datasets/ausapis/australian-companies)

Direct CSV downloads (always the latest):

- [companies.csv](https://huggingface.co/datasets/ausapis/australian-companies/resolve/main/companies.csv)
- [former_names.csv](https://huggingface.co/datasets/ausapis/australian-companies/resolve/main/former_names.csv)

Need live ABN and ACN lookups instead of a weekly file? [AUO Business Data API](https://auo.com.au) joins every free Australian government business register into one API, with KYB verification, sanctions screening and change monitoring.

## Why this exists

ASIC publishes the register on data.gov.au as a `.csv` that is actually tab-separated, with one row per company *name* (so renamed companies appear several times), DD/MM/YYYY dates, `0` for a missing ABN, and a file name that changes every month. This repo turns it into two tidy CSVs:

| File | Rows | Contents |
|---|---|---|
| `companies.csv` | one per ACN | `acn, abn, name, name_start_date, type, class, sub_class, status, registration_date, deregistration_date, previous_state, state_registration_number` |
| `former_names.csv` | one per former name | `acn, former_name` |

Column and code definitions are in the [dataset card](dataset-card.md).

## How it updates

A GitHub Action ([`.github/workflows/sync.yml`](.github/workflows/sync.yml)) runs every Wednesday, after ASIC's Tuesday upload:

1. Looks up the current Company Dataset zip through the data.gov.au API.
2. Runs [`build.py`](build.py) to reshape it, failing loudly if ASIC's format changes.
3. Pushes the CSVs and dataset card to Hugging Face. Unchanged files are skipped.

Kaggle pulls the Hugging Face files on its own schedule.

Run it locally:

```bash
pip install -r requirements.txt
python build.py --out-dir out            # build only
HF_TOKEN=hf_... python build.py --push   # build and upload
```

## Source and licence

Data: Australian Securities and Investments Commission (ASIC), [Company Dataset](https://data.gov.au/data/dataset/asic-companies), licensed under [CC BY 3.0 AU](https://creativecommons.org/licenses/by/3.0/au/). Reshaped as described above; no records added or removed. Not affiliated with or endorsed by ASIC.
