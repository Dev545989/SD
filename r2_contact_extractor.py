#!/usr/bin/env python3
"""
R2 Contact Info Extractor — Daily Incremental (by name only)
=============================================================
Usage: python r2_contact_extractor.py <YYYY-MM-DD>

Logic:
  1. Extracts today's contact_info from all .xlsx files
  2. Deduplicates TODAY only by 'name' (keeps first occurrence)
  3. Reads previous day's agent-agency.xlsx
  4. Appends: previous + today (today last so it wins on name clash)
  5. Deduplicates MERGED list by 'name' (newest wins)
  6. Flattens JSON and writes to R2
"""

import os
import sys
import json
import ast
import re
import boto3
import pandas as pd
from io import BytesIO
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


def normalize_category(cat: str) -> str:
    """Normalize category name: lowercase, replace spaces/& with hyphens."""
    cat = cat.lower().strip()
    cat = re.sub(r'[\s&]+', '-', cat)
    cat = re.sub(r'-+', '-', cat)
    cat = cat.strip('-')
    return cat


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


def safe_parse_dict(raw: str):
    raw = str(raw).strip()
    if not raw or raw.lower() in ("contact_info", "nan", "none", "nan", "null", ""):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        pass
    return None


def has_valid_phone(contact: dict) -> bool:
    """
    Return True if the contact dict contains at least one real phone number.
    Checks: mobile, whatsapp, proxyMobile, and mobileNumbers list.
    """
    if not isinstance(contact, dict):
        return False

    # Check scalar phone fields
    for key in ("mobile", "whatsapp", "proxyMobile"):
        val = contact.get(key)
        if val is None:
            continue
        val_str = str(val).strip()
        if val_str and val_str.lower() not in ("n/a", "null", "none", "nan", ""):
            digits = re.sub(r'\D', '', val_str)
            if len(digits) >= 7:
                return True

    # Check mobileNumbers list
    mobile_numbers = contact.get("mobileNumbers")
    if isinstance(mobile_numbers, str):
        try:
            mobile_numbers = json.loads(mobile_numbers.replace("'", '"'))
        except Exception:
            mobile_numbers = None
    if isinstance(mobile_numbers, list):
        for num in mobile_numbers:
            digits = re.sub(r'\D', '', str(num))
            if len(digits) >= 7:
                return True

    return False


def is_valid_contact(contact: dict) -> bool:
    """
    A valid contact must have:
      - A non-empty name
      - At least one valid phone number
    """
    if not isinstance(contact, dict):
        return False

    name = str(contact.get("name") or "").strip()
    if not name or name.lower() in ("n/a", "null", "none", "nan"):
        return False

    return has_valid_phone(contact)


def extract_contacts_from_sheets(sheets: dict):
    contacts = []
    skipped = 0
    for sheet_name, df in sheets.items():
        if df.empty:
            continue

        contact_col = None
        for col in df.columns:
            if str(col).strip() == 'contact_info':
                contact_col = col
                break
        if contact_col is None:
            for col in df.columns:
                if str(col).strip().lower() == 'contactinfo':
                    contact_col = col
                    break
        if contact_col is None:
            continue

        for raw in df[contact_col].dropna().astype(str):
            obj = safe_parse_dict(raw)
            if isinstance(obj, dict) and obj.get("name") is not None:
                if is_valid_contact(obj):
                    contacts.append(obj)
                else:
                    skipped += 1
    if skipped:
        print(f"    ⚠️  Skipped {skipped} contact(s) with missing/invalid phone")
    return contacts


def dedup_by_name(contacts: list):
    """
    Deduplicate by 'name'.
    - If a name appears once → keep it.
    - If a name appears multiple times → prefer the one WITH a valid phone.
      If both have phones (or both don't), keep the LAST occurrence (newest).
    """
    seen = {}
    for c in contacts:
        name = str(c.get("name") or "").strip()
        if not name:
            continue

        existing = seen.get(name)
        if existing is None:
            seen[name] = c
            continue

        existing_has_phone = has_valid_phone(existing)
        current_has_phone = has_valid_phone(c)

        # Prefer contact with valid phone over empty one
        if current_has_phone and not existing_has_phone:
            seen[name] = c
        # If both valid or both invalid, keep last (newest wins)
        elif current_has_phone == existing_has_phone:
            seen[name] = c

    return list(seen.values())


def read_previous_contacts(prev_key: str):
    """
    Read flattened agent-agency.xlsx and convert back to list of dicts.
    """
    try:
        resp = s3.get_object(Bucket=BUCKET_NAME, Key=prev_key)
        data = resp["Body"].read()
        df = pd.read_excel(BytesIO(data))
        if df.empty:
            return []

        contacts = []
        for _, row in df.iterrows():
            record = {}
            for col in df.columns:
                val = row[col]
                if pd.isna(val):
                    record[col] = None
                else:
                    record[col] = val

            # Re-hydrate list columns from string representation if needed
            for list_col in ["mobileNumbers", "roles"]:
                if list_col in record and isinstance(record[list_col], str):
                    try:
                        record[list_col] = json.loads(record[list_col].replace("'", '"'))
                    except Exception:
                        record[list_col] = []
                elif list_col in record and record[list_col] is None:
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
        # Flatten nested dicts/lists into columns
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
    # Normalize category name for consistent output paths
    norm_cat = normalize_category(category)
    cat_prefix = f"{day_prefix}{category}/"
    excel_keys = list_all_excel_keys(cat_prefix)

    # 1. Extract today's contacts (filtered for valid phones)
    day_contacts = []
    for key in excel_keys:
        sheets = read_excel_sheets(key)
        day_contacts.extend(extract_contacts_from_sheets(sheets))

    # 2. Deduplicate TODAY by name (prefer ones with phones)
    day_unique = dedup_by_name(day_contacts)

    # 3. Read previous day's merged file
    prev_key = f"{prev_day_prefix}{OUTPUT_SUBDIR}/{norm_cat}/{OUTPUT_FILENAME}"
    prev_contacts = read_previous_contacts(prev_key)

    # Filter previous contacts too — remove stale records with no valid phone
    prev_contacts = [c for c in prev_contacts if is_valid_contact(c)]

    # 4. Merge: previous first, then today (today wins on name clash,
    #    but dedup_by_name will prefer the one with a phone regardless of order)
    merged = dedup_by_name(prev_contacts + day_unique)

    # 5. Write to R2
    output_key = f"{day_prefix}{OUTPUT_SUBDIR}/{norm_cat}/{OUTPUT_FILENAME}"
    n_rows = write_contacts_excel(merged, output_key)

    print(f"    → {category}: {len(day_unique)} new valid | {len(prev_contacts)} prev valid | {len(merged)} total unique")
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
        sys.exit(1)
    target_date = sys.argv[1]
    main(target_date)