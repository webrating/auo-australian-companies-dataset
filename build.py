"""Build the Australian companies dataset (ASIC register) and push it to Hugging Face.

Finds the current ASIC Company Dataset on data.gov.au, downloads the zip and
turns ASIC's tab-separated extract (one row per company *name*, DD/MM/YYYY
dates, ABN "0" for none) into two tidy CSVs:

    companies.csv      one row per ACN: current name, ABN, type, status, dates
    former_names.csv   one row per former company name (acn, former_name)

    python build.py                # build into the current directory only
    python build.py --push         # build, then upload both CSVs + dataset card

--push needs HF_TOKEN (a write token). HF_DATASET_REPO overrides the target.
Unchanged files are skipped by huggingface_hub, so weeks where ASIC's data is
identical produce no commit.
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


def push(out_dir: Path, source_url: str) -> None:
    from huggingface_hub import CommitOperationAdd, HfApi

    repo = os.environ.get("HF_DATASET_REPO", DEFAULT_REPO)
    info = HfApi(token=os.environ["HF_TOKEN"]).create_commit(
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true", help="upload to Hugging Face")
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
        push(args.out_dir, url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
