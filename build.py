"""Build the Australian companies dataset (ASIC register) and push it to Hugging Face.

Finds the current ASIC Company Dataset on data.gov.au, downloads the zip and
turns ASIC's tab-separated extract (one row per company *name*, DD/MM/YYYY
dates, ABN "0" for none) into two tidy CSVs:

    companies.csv      one row per ACN: current name, ABN, type, status, dates
    former_names.csv   one row per former company name (acn, former_name)

    python build.py                # build into the current directory only
    python build.py --push         # build, then upload both CSVs + dataset card
    python build.py --push --kaggle   # ...and upload a new Kaggle version

--push needs HF_TOKEN (a write token). HF_DATASET_REPO overrides the target.
--kaggle needs KAGGLE_API_TOKEN. Unchanged files are skipped by huggingface_hub,
so weeks where ASIC's data is identical produce no commit on either site.
"""

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PACKAGE_URL = "https://data.gov.au/data/api/3/action/package_show?id=asic-companies"
DEFAULT_REPO = "ausapis/australian-companies"
COMPANIES = "companies.csv"
FORMER_NAMES = "former_names.csv"
HERE = Path(__file__).parent

# Sanity gates so a changed ASIC format fails the run instead of pushing junk.
MIN_COMPANIES = 3_500_000
MAX_STALE_DAYS = 21

SOURCE_COLUMNS = [
    "Company Name", "ACN", "Type", "Class", "Sub Class", "Status",
    "Date of Registration", "Date of Deregistration",
    "Previous State of Registration", "State Registration number",
    "Modified since last report", "Current Name Indicator", "ABN",
    "Current Name", "Current Name Start Date",
]
(NAME, ACN, TYPE, CLASS, SUB_CLASS, STATUS, REG_DATE, DEREG_DATE, PREV_STATE,
 STATE_REG_NUM, _MODIFIED, CURRENT_FLAG, ABN, CURRENT_NAME, NAME_START) = range(len(SOURCE_COLUMNS))

COMPANY_COLUMNS = [
    "acn", "abn", "name", "name_start_date", "type", "class", "sub_class",
    "status", "registration_date", "deregistration_date", "previous_state",
    "state_registration_number",
]


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "australian-companies-dataset"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def find_current_zip() -> tuple[str, datetime]:
    """The zip's URL changes monthly (company_YYYYMM.zip), so look it up each run."""
    resources = fetch_json(PACKAGE_URL)["result"]["resources"]
    matches = [
        r for r in resources
        if r.get("format", "").upper() == "ZIP" and "current" in r.get("name", "").lower()
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one current ZIP resource, found {[r.get('name') for r in matches]}")
    modified = matches[0].get("last_modified") or matches[0]["created"]
    return matches[0]["url"], datetime.fromisoformat(modified).replace(tzinfo=timezone.utc)


def iso(value: str) -> str:
    value = value.strip()
    return datetime.strptime(value, "%d/%m/%Y").date().isoformat() if value else ""


def groups_by_acn(reader):
    """Yield (acn, rows) per company. ASIC's file is sorted by ACN; enforce it."""
    last, group = None, []
    for row in reader:
        if len(row) != len(SOURCE_COLUMNS):
            raise ValueError(f"Row has {len(row)} fields, expected {len(SOURCE_COLUMNS)}: {row[:3]}")
        acn = row[ACN]
        if acn != last:
            if last is not None:
                if acn < last:
                    raise ValueError(f"ASIC file is no longer sorted by ACN ({acn} after {last})")
                yield last, group
            last, group = acn, []
        group.append(row)
    if last is not None:
        yield last, group


def build(source_zip: Path, out_dir: Path) -> tuple[int, int]:
    with zipfile.ZipFile(source_zip) as z:
        (member,) = z.namelist()
        text = io.TextIOWrapper(z.open(member), encoding="utf-8-sig", newline="")
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader)
        if header != SOURCE_COLUMNS:
            raise ValueError(f"ASIC columns changed: {header}")

        with (out_dir / COMPANIES).open("w", newline="", encoding="utf-8") as cf, \
             (out_dir / FORMER_NAMES).open("w", newline="", encoding="utf-8") as ff:
            companies = csv.writer(cf, lineterminator="\n")
            former = csv.writer(ff, lineterminator="\n")
            companies.writerow(COMPANY_COLUMNS)
            former.writerow(["acn", "former_name"])

            company_count = former_count = 0
            for acn, rows in groups_by_acn(reader):
                current = [r for r in rows if r[CURRENT_FLAG].strip() == "Y"]
                if len(current) > 1:
                    raise ValueError(f"ACN {acn} has {len(current)} current names")
                if current:
                    base, name = current[0], current[0][NAME].strip()
                    old_rows = [r for r in rows if r is not base]
                else:
                    # A handful of cancelled (CNCL) companies only have history
                    # rows; their current name lives in the Current Name column.
                    base, name = rows[0], rows[0][CURRENT_NAME].strip()
                    old_rows = rows

                abn = base[ABN].strip()
                name_start = next((r[NAME_START] for r in rows if r[NAME_START].strip()), "")
                companies.writerow([
                    acn, "" if abn == "0" else abn, name, iso(name_start),
                    base[TYPE].strip(), base[CLASS].strip(), base[SUB_CLASS].strip(),
                    base[STATUS].strip(), iso(base[REG_DATE]), iso(base[DEREG_DATE]),
                    base[PREV_STATE].strip(), base[STATE_REG_NUM].strip(),
                ])
                company_count += 1

                for old in dict.fromkeys(r[NAME].strip() for r in old_rows):
                    if old and old != name:
                        former.writerow([acn, old])
                        former_count += 1

    if company_count < MIN_COMPANIES:
        raise ValueError(f"Only {company_count} companies, expected at least {MIN_COMPANIES}")
    return company_count, former_count


def push(out_dir: Path, source_url: str) -> bool:
    """Upload to Hugging Face. Returns True if a new commit was made."""
    from huggingface_hub import CommitOperationAdd, HfApi

    repo = os.environ.get("HF_DATASET_REPO", DEFAULT_REPO)
    api = HfApi(token=os.environ["HF_TOKEN"])
    before = api.repo_info(repo, repo_type="dataset").sha
    info = api.create_commit(
        repo_id=repo,
        repo_type="dataset",
        operations=[
            CommitOperationAdd(path_in_repo=COMPANIES, path_or_fileobj=str(out_dir / COMPANIES)),
            CommitOperationAdd(path_in_repo=FORMER_NAMES, path_or_fileobj=str(out_dir / FORMER_NAMES)),
            CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=str(HERE / "dataset-card.md")),
        ],
        commit_message=f"Sync ASIC company register ({source_url.rsplit('/', 1)[-1]})",
    )
    print(f"Hugging Face: {info.commit_url}")
    return info.oid != before


# File and column descriptions shown on Kaggle (and counted in its usability score).
KAGGLE_RESOURCES = [
    {
        "path": COMPANIES,
        "description": "One row per company on the ASIC register (about 4 million): ACN, ABN, "
                       "current name, type, class, status and registration dates.",
        "schema": {"fields": [
            {"name": "acn", "type": "string", "description": "Australian Company Number, 9 digits with leading zeros. For type RACN this is the ARBN."},
            {"name": "abn", "type": "string", "description": "Australian Business Number, 11 digits. Blank if the company has no ABN."},
            {"name": "name", "type": "string", "description": "Current company name."},
            {"name": "name_start_date", "type": "datetime", "description": "Date the current name took effect (only for renamed companies)."},
            {"name": "type", "type": "string", "description": "Company type: APTY proprietary, APUB public, FNOS foreign, RACN registered Australian body, CCIV collective investment vehicle."},
            {"name": "class", "type": "string", "description": "Liability class: LMSH shares, LMGT guarantee, LMSG shares and guarantee, UNLM unlimited, NLIA no liability, NONE."},
            {"name": "sub_class", "type": "string", "description": "Sub class code, e.g. PROP proprietary other, PSTC superannuation trustee, LIST listed public, ULST unlisted public."},
            {"name": "status", "type": "string", "description": "REGD registered, DRGD deregistered, SOFF strike-off in progress, EXAD external administration, NOAC not active, CNCL cancelled, DISS dissolved."},
            {"name": "registration_date", "type": "datetime", "description": "Date the company was registered (YYYY-MM-DD)."},
            {"name": "deregistration_date", "type": "datetime", "description": "Date the company was deregistered, if any."},
            {"name": "previous_state", "type": "string", "description": "State of original registration, for companies registered before national registration."},
            {"name": "state_registration_number", "type": "string", "description": "Registration number assigned by that state."},
        ]},
    },
    {
        "path": FORMER_NAMES,
        "description": "One row per former company name (about 430,000). Join to companies.csv on acn.",
        "schema": {"fields": [
            {"name": "acn", "type": "string", "description": "ACN of the company, matching companies.csv."},
            {"name": "former_name", "type": "string", "description": "A name the company previously held."},
        ]},
    },
]

KAGGLE_PROVENANCE = (
    "Source: Australian Securities and Investments Commission (ASIC), Company Dataset on data.gov.au "
    "(https://data.gov.au/data/dataset/asic-companies), licensed under CC BY 3.0 AU. ASIC uploads a "
    "register snapshot every Tuesday. A GitHub Action "
    "(https://github.com/webrating/auo-australian-companies-dataset) downloads it every Wednesday and "
    "reshapes it: one row per company instead of one row per company name, former names split into "
    "former_names.csv, dates converted to ISO 8601, ABN 0 replaced with blank, whitespace trimmed. "
    "No records are added or removed."
)


def push_kaggle(out_dir: Path, source_url: str, changed: bool) -> None:
    """Upload to Kaggle directly. Kaggle's "Remote URL" import can't follow
    Hugging Face's redirect to its CDN, so it can't pull the files itself."""
    import time

    from kaggle.api.kaggle_api_extended import KaggleApi
    from requests.exceptions import HTTPError

    api = KaggleApi()
    api.authenticate()
    ref = os.environ.get("KAGGLE_DATASET", DEFAULT_REPO)
    # dataset_status returns 403 while a new upload is still processing, so
    # check the owner's dataset list instead.
    owner = ref.split("/")[0]
    exists = any(d.ref == ref for d in api.dataset_list(user=owner))

    card = (HERE / "dataset-card.md").read_text(encoding="utf-8")
    metadata = {
        "id": ref,
        "title": "Australian Companies Data (ASIC Company Register)",
        "subtitle": "Every ASIC-registered Australian company: ACN, ABN, status, dates, former names",
        "description": card.split("---", 2)[2].strip(),
        "licenses": [{"name": "other"}],
        "keywords": ["business", "australia", "finance"],
        "resources": KAGGLE_RESOURCES,
        "userSpecifiedSources": KAGGLE_PROVENANCE,
        "expectedUpdateFrequency": "weekly",
    }
    (out_dir / "dataset-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    if not exists or changed:
        if exists:
            resp = api.dataset_create_version(
                str(out_dir), f"Weekly ASIC sync ({source_url.rsplit('/', 1)[-1]})", quiet=False, dir_mode="skip"
            )
        else:
            resp = api.dataset_create_new(str(out_dir), public=True, quiet=False, dir_mode="skip")
        status = (getattr(resp, "status", None) or "").lower()
        if resp is None or status != "ok":
            raise RuntimeError(
                f"Kaggle upload failed: status={status!r} error={getattr(resp, 'error', None)!r} "
                f"invalid_tags={getattr(resp, 'invalid_tags', None)!r}"
            )
        print(f"Kaggle: upload accepted, processing at {resp.url}")
    else:
        print("Kaggle: data unchanged, skipping upload")

    # File and column descriptions are attached to each uploaded version (from
    # "resources"). Provenance and update frequency are only set by a metadata
    # update, which Kaggle rejects (403) while a new version is still
    # processing, so retry for a few minutes.
    for attempt in range(10):
        try:
            api.dataset_metadata_update(ref, str(out_dir))
            print("Kaggle: metadata updated")
            return
        except HTTPError as e:
            if e.response is None or e.response.status_code != 403 or attempt == 9:
                raise
            time.sleep(30)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true", help="upload to Hugging Face")
    parser.add_argument("--kaggle", action="store_true", help="with --push, also upload to Kaggle")
    parser.add_argument("--force-kaggle", action="store_true", help="upload a Kaggle version even if unchanged")
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    parser.add_argument("--source-zip", type=Path, help="use a local ASIC zip instead of downloading")
    args = parser.parse_args()

    url, modified = find_current_zip()
    age = datetime.now(timezone.utc) - modified
    print(f"ASIC source: {url} (updated {modified:%Y-%m-%d})")
    if age > timedelta(days=MAX_STALE_DAYS):
        raise ValueError(f"ASIC data last updated {age.days} days ago, more than {MAX_STALE_DAYS}")

    with tempfile.TemporaryDirectory() as tmp:
        source = args.source_zip
        if source is None:
            source = Path(tmp) / "company.zip"
            req = urllib.request.Request(url, headers={"User-Agent": "australian-companies-dataset"})
            with urllib.request.urlopen(req, timeout=300) as resp, source.open("wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)

        args.out_dir.mkdir(parents=True, exist_ok=True)
        companies, former = build(source, args.out_dir)
    print(f"Built {COMPANIES} ({companies} companies) and {FORMER_NAMES} ({former} former names)")

    if args.push:
        changed = push(args.out_dir, url)
        if args.kaggle:
            push_kaggle(args.out_dir, url, changed or args.force_kaggle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
