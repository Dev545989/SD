#!/usr/bin/env python3
"""
R2 Contact Info Extractor — Daily Incremental
==============================================
Usage: python r2_contact_extractor.py <YYYY-MM-DD>

Example: python r2_contact_extractor.py 2026-08-11

For each category under DKSA/year=YYYY/month=MM/day=DD/:
  1. Reads all .xlsx files recursively (multi-sheet, with headers)
  2. Extracts contact_info JSON from the 'contact_info' column
  3. Filters out entries where name is null
  4. Deduplicates by mobile number
  5. If previous day has agent-agency/category/agent-agency.xlsx, merges with it
  6. Writes to DKSA/year=YYYY/month=MM/day=DD/agent-agency/<category>/agent-agency.xlsx

Environment variables:
    CF_R2_ENDPOINT_URL
    CF_R2_ACCESS_KEY
    CF_R2_SECRET_KEY
    R2_BUCKET_NAME
"""

import os
import sys
import json
import boto3
import pandas as pd
from io import BytesIO
from collections import OrderedDict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CF_R2_ACCESS_KEY = os.getenv("CF_R2_ACCESS_KEY_ID")
CF_R2_SECRET_KEY = os.getenv("CF_R2_SECRET_ACCESS_KEY")
CF_R2_ENDPOINT_URL = os.getenv("CF_R2_ENDPOINT_URL")
BUCKET_NAME = os.getenv("CF_R2_BUCKET_NAME", "")
BASE_PREFIX     = "DKSA/"
OUTPUT_SUBDIR   = "agent-agency"
OUTPUT_FILENAME = "agent-agency.xlsx"

if not all([CF_R2_ENDPOINT_URL, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY, BUCKET_NAME]):
    print("ERROR: Set CF_R2_ENDPOINT_URL, CF_R2_ACCESS_KEY_ID, CF_R2_SECRET_ACCESS_KEY, CF_R2_BUCKET_NAME")
    sys.exit(1)

s3 = boto3.client(
    "s3",
    endpoint_url=CF_R2_ENDPOINT_URL,
    aws_access_key_id=CF_R2_ACCESS_KEY,
    aws_secret_access_key=CF_R2_SECRET_KEY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date(date_str: str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.year:04d}", f"{dt.month:02d}", f"{dt.day:02d}"


def get_day_prefix(year: str, month: str, day: str):
    return f"{BASE_PREFIX}year={year}/month={month}/day={day}/"


def get_prev_day_prefix(year: str, month: str, day: str):
    dt = datetime(int(year), int(month), int(day)) - timedelta(days=1)
    return get_day_prefix(f"{dt.year:04d}", f"{dt.month:02d}", f"{dt.day:02d}")


def list_folders(prefix: str):
    folders = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            folders.append(cp["Prefix"])
    return sorted(folders)


def list_all_excel_keys(prefix: str):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.endswith(".xlsx") and not k.split("/")[-1].startswith("~$"):
                keys.append(k)
    return keys


def read_excel_sheets(key: str):
    try:
        resp = s3.get_object(Bucket=BUCKET_NAME, Key=key)
        data = resp["Body"].read()
        xl = pd.ExcelFile(BytesIO(data))
        return {name: xl.parse(name) for name in xl.sheet_names}
    except Exception as e:
        print(f"  ⚠️  Failed to read {key}: {e}")
        return {}


def extract_contacts_from_sheets(sheets: dict, file_key: str = ""):
    contacts = []
    for sheet_name, df in sheets.items():
        if df.empty:
            print(f"    [DEBUG] Sheet '{sheet_name}' is EMPTY")
            continue

        print(f"    [DEBUG] Sheet '{sheet_name}': shape={df.shape}")
        print(f"    [DEBUG] Columns: {list(df.columns)}")

        contact_col = None
        for col in df.columns:
            col_str = str(col).strip().lower()
            if col_str in ('contact_info', 'contactinfo'):
                contact_col = col
                break

        if contact_col is None:
            print(f"    [DEBUG] ❌ No contact_info column found!")
            continue

        col_values = df[contact_col].dropna().astype(str)
        print(f"    [DEBUG] ✅ Column '{contact_col}' has {len(col_values)} non-null values")

        parsed_count = 0
        skipped_count = 0
        null_name_count = 0

        for i, raw in enumerate(col_values):
            raw = raw.strip()

            if not raw or raw in ("contact_info", "nan", "None", "NaN", "null"):
                skipped_count += 1
                continue

            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                skipped_count += 1
                if i < 2:
                    print(f"    [DEBUG]    Row {i}: JSON parse error → '{raw[:60]}...'")
                continue

            if isinstance(obj, dict):
                if obj.get("name") is not None:
                    contacts.append(obj)
                    parsed_count += 1
                    if parsed_count <= 2:
                        print(f"    [DEBUG]    ✅ EXTRACTED: name={obj.get('name')}, mobile={obj.get('mobile')}")
                else:
                    null_name_count += 1
                    if null_name_count <= 2:
                        print(f"    [DEBUG]    ⏭️  SKIPPED (name is null)")
            else:
                skipped_count += 1

        print(f"    [DEBUG] Summary: {parsed_count} extracted, {null_name_count} null-name, {skipped_count} skipped")

    return contacts


def dedup_contacts(contacts: list):
    seen = OrderedDict()
    for c in contacts:
        key = c.get("mobile") or c.get("whatsapp") or c.get("name")
        if key and key not in seen:
            seen[key] = c
    return list(seen.values())


def read_previous_contacts(prev_key: str):
    try:
        resp = s3.get_object(Bucket=BUCKET_NAME, Key=prev_key)
        data = resp["Body"].read()
        df = pd.read_excel(BytesIO(data))
        contacts = []
        for _, row in df.iterrows():
            record = row.to_dict()
            record = {k: (v if pd.notna(v) else None) for k, v in record.items()}
            for list_col in ["mobileNumbers", "roles"]:
                if list_col in record and isinstance(record[list_col], str):
                    try:
                        record[list_col] = json.loads(record[list_col].replace("'", '"'))
                    except:
                        record[list_col] = []
            contacts.append(record)
        return contacts
    except s3.exceptions.NoSuchKey:
        return []
    except Exception as e:
        print(f"  ⚠️  Failed to read previous {prev_key}: {e}")
        return []


def write_contacts_excel(contacts: list, output_key: str):
    if not contacts:
        df = pd.DataFrame(columns=["name", "mobile", "whatsapp", "proxyMobile",
                                    "mobileNumbers", "roles"])
    else:
        df = pd.json_normalize(contacts)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Contacts")
    buf.seek(0)
    s3.put_object(Bucket=BUCKET_NAME, Key=output_key, Body=buf.getvalue())
    return len(df)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def get_categories(day_prefix: str):
    cats = []
    for p in list_folders(day_prefix):
        cat = p.replace(day_prefix, "").strip("/")
        if cat and cat != OUTPUT_SUBDIR:
            cats.append(cat)
    return sorted(cats)


def process_category(day_prefix: str, category: str, prev_day_prefix: str):
    cat_prefix = f"{day_prefix}{category}/"
    excel_keys = list_all_excel_keys(cat_prefix)

    print(f"    Found {len(excel_keys)} Excel file(s)")
    for k in excel_keys:
        print(f"      📄 {k}")

    day_contacts = []
    for key in excel_keys:
        sheets = read_excel_sheets(key)
        day_contacts.extend(extract_contacts_from_sheets(sheets, key))

    day_unique = dedup_contacts(day_contacts)

    prev_key = f"{prev_day_prefix}{OUTPUT_SUBDIR}/{category}/{OUTPUT_FILENAME}"
    prev_contacts = read_previous_contacts(prev_key)

    merged = dedup_contacts(prev_contacts + day_unique)

    output_key = f"{day_prefix}{OUTPUT_SUBDIR}/{category}/{OUTPUT_FILENAME}"
    n_rows = write_contacts_excel(merged, output_key)

    print(f"    → {category}: {len(day_unique)} new | {len(prev_contacts)} prev | {len(merged)} total")
    return len(merged)


def main(target_date: str):
    year, month, day = parse_date(target_date)
    day_prefix = get_day_prefix(year, month, day)
    prev_day_prefix = get_prev_day_prefix(year, month, day)

    print(f"📅 Target:  {day_prefix}")
    print(f"📅 Previous: {prev_day_prefix}")

    categories = get_categories(day_prefix)
    if not categories:
        print("   (no categories found)")
        return

    for cat in categories:
        process_category(day_prefix, cat, prev_day_prefix)

    print("\n✅ Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python r2_contact_extractor.py <YYYY-MM-DD>")
        print("Example: python r2_contact_extractor.py 2026-08-11")
        sys.exit(1)

    target_date = sys.argv[1]
    main(target_date)