#!/usr/bin/env python3
"""
R2 Contact Info Extractor — Daily Incremental
==============================================
Usage: python r2_contact_extractor.py <YYYY-MM-DD>

Example: python r2_contact_extractor.py 2026-08-11

For each category under DKSA/year=YYYY/month=MM/day=DD/:
  1. Reads all .xlsx files recursively
  2. Extracts contact_info from column A (skips null names)
  3. Deduplicates by mobile/whatsapp/name
  4. If previous day has agent-agency/category/agent-agency.xlsx, merges with it
  5. Writes to DKSA/year=YYYY/month=MM/day=DD/agent-agency/<category>/agent-agency.xlsx

Environment variables:
    R2_ENDPOINT_URL
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
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
R2_ENDPOINT     = os.getenv("R2_ENDPOINT_URL", "").rstrip("/")
R2_ACCESS_KEY   = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY   = os.getenv("R2_SECRET_ACCESS_KEY", "")
BUCKET_NAME     = os.getenv("R2_BUCKET_NAME", "")
BASE_PREFIX     = "DKSA/"
OUTPUT_SUBDIR   = "agent-agency"
OUTPUT_FILENAME = "agent-agency.xlsx"

if not all([R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, BUCKET_NAME]):
    print("ERROR: Set R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME")
    sys.exit(1)

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date(date_str: str):
    """Parse 'YYYY-MM-DD' into (year, month, day) strings."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return (
        f"{dt.year:04d}",
        f"{dt.month:02d}",
        f"{dt.day:02d}",
    )


def get_day_prefix(year: str, month: str, day: str):
    return f"{BASE_PREFIX}year={year}/month={month}/day={day}/"


def get_prev_day_prefix(year: str, month: str, day: str):
    """Return prefix for the previous calendar day."""
    dt = datetime(int(year), int(month), int(day)) - timedelta(days=1)
    return get_day_prefix(
        f"{dt.year:04d}",
        f"{dt.month:02d}",
        f"{dt.day:02d}",
    )


def list_folders(prefix: str):
    """Return sorted list of folder prefixes under given prefix."""
    folders = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            folders.append(cp["Prefix"])
    return sorted(folders)


def list_all_excel_keys(prefix: str):
    """Recursively list every .xlsx key under prefix (skip temp ~$ files)."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.endswith(".xlsx") and not k.split("/")[-1].startswith("~$"):
                keys.append(k)
    return keys


def read_excel_sheets(key: str):
    """Download Excel from R2 and return {sheet_name: DataFrame}."""
    try:
        resp = s3.get_object(Bucket=BUCKET_NAME, Key=key)
        data = resp["Body"].read()
        xl = pd.ExcelFile(BytesIO(data))
        return {name: xl.parse(name, header=None) for name in xl.sheet_names}
    except Exception as e:
        print(f"  ⚠️  Failed to read {key}: {e}")
        return {}


def extract_contacts_from_sheets(sheets: dict):
    """Parse column A from every sheet, yield contact dicts (skip null names)."""
    contacts = []
    for sheet_name, df in sheets.items():
        if df.empty or df.shape[1] < 1:
            continue
        col_a = df.iloc[:, 0].astype(str)
        for raw in col_a:
            raw = raw.strip()
            if raw in ("contact_info", "nan", "None", "", "NaN"):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("name") is not None:
                contacts.append(obj)
    return contacts


def dedup_contacts(contacts: list):
    """Deduplicate by mobile → whatsapp → name. Keep first seen."""
    seen = OrderedDict()
    for c in contacts:
        key = c.get("mobile") or c.get("whatsapp") or c.get("name")
        if key and key not in seen:
            seen[key] = c
    return list(seen.values())


def read_previous_contacts(prev_key: str):
    """Read agent-agency.xlsx from previous day if it exists."""
    try:
        resp = s3.get_object(Bucket=BUCKET_NAME, Key=prev_key)
        data = resp["Body"].read()
        df = pd.read_excel(BytesIO(data))
        # Convert DataFrame rows back to dicts
        contacts = []
        for _, row in df.iterrows():
            record = row.to_dict()
            # Handle NaN values
            record = {k: (v if pd.notna(v) else None) for k, v in record.items()}
            # Convert list-like strings back to lists if needed
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
    """Upload .xlsx of contacts to R2."""
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
    """Return category folder names directly under a day prefix."""
    cats = []
    for p in list_folders(day_prefix):
        cat = p.replace(day_prefix, "").strip("/")
        if cat and cat != OUTPUT_SUBDIR:
            cats.append(cat)
    return sorted(cats)


def process_category(day_prefix: str, category: str, prev_day_prefix: str):
    """
    1. Read all .xlsx under day_prefix/category/ (recursive)
    2. Extract & dedup contacts for THIS day
    3. Read previous day's agent-agency.xlsx if exists
    4. Merge (cumulative) and dedup again
    5. Write to day_prefix/agent-agency/category/agent-agency.xlsx
    Returns number of total contacts written.
    """
    cat_prefix = f"{day_prefix}{category}/"
    excel_keys = list_all_excel_keys(cat_prefix)

    # Extract today's contacts
    day_contacts = []
    for key in excel_keys:
        sheets = read_excel_sheets(key)
        day_contacts.extend(extract_contacts_from_sheets(sheets))

    day_unique = dedup_contacts(day_contacts)

    # Read previous day's cumulative file
    prev_key = f"{prev_day_prefix}{OUTPUT_SUBDIR}/{category}/{OUTPUT_FILENAME}"
    prev_contacts = read_previous_contacts(prev_key)

    # Merge and dedup
    merged = dedup_contacts(prev_contacts + day_unique)

    # Write output
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