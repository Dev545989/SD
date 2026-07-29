import argparse
import ast
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
import random
import time
import pandas as pd
import requests as req
from PIL import Image
import glob
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from text_utils import clean_text, sanitize_filename
from contact_info_fetcher import build_ad_url, fetch_contact_info, EMPTY_CONTACT_INFO
from r2_uploader import upload_buffer

THUMB_URL_TEMPLATE = "https://images.dubizzle.sa/thumbnails/{photo_id}-800x600.webp"

COLUMNS_TO_DROP = ['geo_point', 'price', 'title_l1', 'description_l1', 'slug_l1', 'coverPhoto',
                   'external_link', 'external_link_l1', 'documentsTags', 'videoCount', 'documentCount'
                   'panoramaCount']


VEHICLE_MANUFACTURER_SPLIT_SLUGS = {"cars-for-sale", "cars-for-rent"}

TIMESTAMP_FIELDS = ("createdAt", "updatedAt", "timestamp")

def parse_formatted_extra_fields(record) -> dict:
    field = record.get("formattedExtraFields")

    if isinstance(field, str):
        try:
            field = ast.literal_eval(field)
        except (ValueError, SyntaxError):
            field = []

    if not isinstance(field, list):
        return {}

    result = {}
    for item in field:
        if isinstance(item, dict):
            attr = item.get("attribute")
            val = item.get("formattedValue_l1") or item.get("formattedValue")
            if attr and val is not None:
                result[attr] = val

    return result


def parse_category(cat_field):
    if isinstance(cat_field, list):
        cats = cat_field
    elif isinstance(cat_field, str):
        try:
            cats = ast.literal_eval(cat_field)
        except (ValueError, SyntaxError):
            cats = []
    else:
        cats = []

    by_level = {c.get("level"): c for c in cats if isinstance(c, dict)}
    return by_level.get(0), by_level.get(1), by_level.get(2)


def sheet_name_for(cat1: dict | None, cat2: dict | None) -> str:
    """Used only for the flat (no sub-subcategories anywhere) combined-file case."""
    if cat1 is None:
        name = "Uncategorized"
    else:
        name = cat1.get("name_l1") or cat1.get("name") or "Uncategorized"
        if cat2:
            sub = cat2.get("name_l1") or cat2.get("name")
            if sub:
                name = f"{name} ({sub})"

    name = clean_text(name)
    name = re.sub(r"[:\\/?*\[\]]", "-", name)
    return name[:31] or "Uncategorized"


def photo_urls(photos_field) -> list:
    if isinstance(photos_field, str):
        try:
            photos_field = ast.literal_eval(photos_field)
        except (ValueError, SyntaxError):
            photos_field = []

    if not photos_field or not isinstance(photos_field, list):
        return []

    urls = []
    for p in photos_field:
        pid = p.get("id") if isinstance(p, dict) else None
        if pid:
            urls.append(THUMB_URL_TEMPLATE.format(photo_id=pid))
    return urls

def format_timestamp(value):
    """Converts a unix epoch (int/float/str like 1784702633.71555) to
    ISO 8601 UTC format: 2025-11-26T07:57:17Z. Leaves non-numeric or
    empty values untouched."""
    if value is None or value == "":
        return value
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return value
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return value
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_timestamp_fields(record: dict) -> dict:
    for field in TIMESTAMP_FIELDS:
        if field in record:
            record[field] = format_timestamp(record[field])
    return record

def download_images(images: list, id_prod: str, category_display: str, dt: datetime = None) -> list:
    r2_paths = []
    uploaded = 0
    failed = 0

    if not images:
        return r2_paths

    file_prefix = id_prod or "unknown"

    for idx, img_url in enumerate(images, start=1):
        filename = f"{file_prefix}-{idx}.webp"
        try:
            r = req.get(img_url, timeout=15)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=100, method=6)
                buf.seek(0)

                r2_key = upload_buffer(
                    buf,
                    filename=filename,
                    category_display=category_display,
                    file_type="images",
                    content_type="image/webp",
                    dt=dt,
                )
                if r2_key:
                    r2_paths.append(r2_key)
                    uploaded += 1
                else:
                    failed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"    [ERROR] {filename} image {idx}: {e}")
            failed += 1

    if uploaded or failed:
        print(f"    {file_prefix}: {uploaded} uploaded, {failed} failed out of {len(images)}")

    return r2_paths


def load_raw(csv_path: str) -> pd.DataFrame | None:
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        return None
    return pd.read_csv(csv_path)


def clean_and_group(df: pd.DataFrame, page=None, dt: datetime = None):
    sheets: dict[str, list] = {}
    all_records = []
    cat0_name_l1 = None
    cat0_name_ar = None
    cat0_slug = None

    for _, row in df.iterrows():
        cat0, cat1, cat2 = parse_category(row.get("category"))
        if cat0 is None:
            continue

        if cat0_name_l1 is None:
            cat0_name_l1 = cat0.get("name_l1")
            cat0_name_ar = cat0.get("name")
            cat0_slug = cat0.get("slug")

        sheet = sheet_name_for(cat1, cat2)

        urls = photo_urls(row.get("photos"))
        ad_id = str(row.get("id") or row.get("externalID") or "")

        image_r2_paths = download_images(urls, id_prod=ad_id, category_display=cat0_name_l1, dt=dt)

        record = row.to_dict()
        record = clean_timestamp_fields(record)
        record["image_r2_paths"] = image_r2_paths
        record["photo_urls"] = urls
        record.pop("photos", None)

        record["image_r2_paths"] = image_r2_paths
        if page is not None:
            ad_url = build_ad_url(record)
            if ad_url:
                record["contact_info"] = fetch_contact_info(page, ad_url)
                time.sleep(random.uniform(2, 5))
            else:
                record["contact_info"] = dict(EMPTY_CONTACT_INFO)
        else:
            record["contact_info"] = dict(EMPTY_CONTACT_INFO)

        sheets.setdefault(sheet, []).append(record)
        all_records.append(record)

    return cat0_name_l1, cat0_name_ar, cat0_slug, sheets, all_records


def _stringify_complex_columns(sheet_df: pd.DataFrame) -> pd.DataFrame:
    for col in sheet_df.columns:
        sheet_df[col] = sheet_df[col].apply(
            lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
        )
    return sheet_df


def safe_sheet_name(name: str, used: set) -> str:
    name = clean_text(name)
    name = re.sub(r"[:\\/?*\[\]]", "-", name)[:31] or "Sheet"

    candidate = name
    n = 1
    while candidate in used:
        suffix = f"~{n}"
        candidate = name[: 31 - len(suffix)] + suffix
        n += 1

    used.add(candidate)
    return candidate


def build_excel(groups: dict) -> io.BytesIO:
    wb = Workbook()
    wb.remove(wb.active)
    used_names: set = set()

    for name, rows in groups.items():
        ws = wb.create_sheet(title=safe_sheet_name(name, used_names))
        sheet_df = _stringify_complex_columns(pd.DataFrame(rows))
        for r in dataframe_to_rows(sheet_df, index=False, header=True):
            ws.append(r)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def group_by_make_model(records: list) -> dict:
    by_make: dict[str, dict[str, list]] = {}

    for record in records:
        extra = parse_formatted_extra_fields(record)
        make = sanitize_filename(extra.get("make"))
        model = clean_text(extra.get("model"))

        by_make.setdefault(make, {}).setdefault(model, []).append(record)

    return by_make


def split_vehicle_records(records: list) -> tuple[dict, dict]:
    manufacturer_groups: dict[str, dict] = {}
    other_sheets: dict[str, list] = {}

    for record in records:
        _, cat1, _ = parse_category(record.get("category"))

        if cat1 is None:
            slug = "uncategorized"
            name = "Uncategorized"
        else:
            slug = cat1.get("slug") or "uncategorized"
            name = clean_text(cat1.get("name_l1") or cat1.get("name") or "Uncategorized")

        if slug in VEHICLE_MANUFACTURER_SPLIT_SLUGS:
            group = manufacturer_groups.setdefault(slug, {"name": name, "records": []})
            group["records"].append(record)
        else:
            other_sheets.setdefault(name, []).append(record)

    return manufacturer_groups, other_sheets


def has_any_subsubcategory(records: list) -> bool:
    """True if any record in this category has a level-2 (sub-subcategory)."""
    for record in records:
        _, _, cat2 = parse_category(record.get("category"))
        if cat2:
            return True
    return False


def build_subcategory_files(records: list) -> dict[str, dict[str, list]]:
    """
    subcategory display name -> {sheet_name: rows}

    Used when the category has sub-subcategories somewhere: each
    subcategory becomes its own file. Inside that file, sheets are named
    after the sub-subcategory when present, otherwise a single sheet named
    after the subcategory itself.
    """
    files: dict[str, dict[str, list]] = {}

    for record in records:
        _, cat1, cat2 = parse_category(record.get("category"))

        if cat1 is None:
            subcat_name = "Uncategorized"
        else:
            subcat_name = clean_text(cat1.get("name_l1") or cat1.get("name") or "Uncategorized")

        if cat2:
            sheet_name = clean_text(cat2.get("name_l1") or cat2.get("name") or subcat_name)
        else:
            sheet_name = subcat_name

        files.setdefault(subcat_name, {}).setdefault(sheet_name, []).append(record)

    return files


def build_category_summary(records: list, cat0_name_l1: str, dt: datetime) -> dict:
    groups: dict[str, dict] = {}

    for record in records:
        _, cat1, cat2 = parse_category(record.get("category"))

        if cat1 is None:
            key = "uncategorized"
            name_en = "Uncategorized"
            name_ar = "غير مصنف"
            slug = "uncategorized"
        else:
            slug = cat1.get("slug") or "uncategorized"
            key = slug
            name_en = cat1.get("name_l1") or cat1.get("name") or "Uncategorized"
            name_ar = cat1.get("name") or name_en

        group = groups.setdefault(key, {
            "name_ar": name_ar,
            "name_en": name_en,
            "slug": slug,
            "listings_count": 0,
            "_sub_seen": set(),
            "subcategories": [],
        })
        group["listings_count"] += 1

        if cat2:
            sub_name = cat2.get("name_l1") or cat2.get("name")
            if sub_name and sub_name not in group["_sub_seen"]:
                group["_sub_seen"].add(sub_name)
                group["subcategories"].append(sub_name)

    subcategories = [
        {
            "name_ar": g["name_ar"],
            "name_en": g["name_en"],
            "slug": g["slug"],
            "listings_count": g["listings_count"],
            "has_subcategories": bool(g["subcategories"]),
            "subcategories": g["subcategories"],
        }
        for g in groups.values()
    ]

    return {
        "scraped_at": dt.isoformat(),
        "data_scraped_date": (dt - timedelta(days=1)).strftime("%Y-%m-%d"),
        "saved_to_R2_date": dt.strftime("%Y-%m-%d"),
        "total_subcategories": len(subcategories),
        "total_listings": len(records),
        "subcategories": subcategories,
    }


def upload_vehicles_by_manufacturer(by_make: dict, category_display: str, subcategory_name: str, dt: datetime):
    print(f"  {subcategory_name} by_manufacturer: {len(by_make)} make(s)")

    excel_file_type = f"excel/{subcategory_name}"
    json_file_type = f"json/{subcategory_name}"

    for make, models in by_make.items():
        total_ads = sum(len(rows) for rows in models.values())
        print(f"    - {make}: {len(models)} model(s), {total_ads} ad(s)")

        excel_buf = build_excel(models)
        excel_key = upload_buffer(
            excel_buf,
            filename=f"{make}.xlsx",
            category_display=category_display,
            file_type=excel_file_type,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            dt=dt,
        )
        print(f"      Excel -> {excel_key}")

        json_bytes = json.dumps(models, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        json_key = upload_buffer(
            io.BytesIO(json_bytes),
            filename=f"{make}.json",
            category_display=category_display,
            file_type=json_file_type,
            content_type="application/json",
            dt=dt,
        )
        print(f"      JSON  -> {json_key}")


def upload_subcategory_files(subcat_files: dict, category_display: str, dt: datetime):
    """
    Uploads each subcategory as its own file, flat under excel/ and json/:
    DKSA/.../{category_display}/excel/{Subcategory Name}.xlsx
    DKSA/.../{category_display}/json/{Subcategory Name}.json
    Sheets inside each file are the sub-subcategories (or a single sheet
    named after the subcategory when it has no sub-subcategories).
    """
    for subcat_name, sheets in subcat_files.items():
        total_ads = sum(len(rows) for rows in sheets.values())
        print(f"  {subcat_name}: {len(sheets)} sheet(s), {total_ads} ad(s)")

        excel_buf = build_excel(sheets)
        excel_key = upload_buffer(
            excel_buf,
            filename=f"{subcat_name}.xlsx",
            category_display=category_display,
            file_type="excel",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            dt=dt,
        )
        print(f"    Excel -> {excel_key}")

        json_bytes = json.dumps(sheets, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        json_key = upload_buffer(
            io.BytesIO(json_bytes),
            filename=f"{subcat_name}.json",
            category_display=category_display,
            file_type="json",
            content_type="application/json",
            dt=dt,
        )
        print(f"    JSON  -> {json_key}")

def remove_category_column(groups):
    for _, rows in groups.items():
        for record in rows:
            record.pop("category", None)

def run(csv_path: str):
    dt = datetime.now()
    df = load_raw(csv_path)

    if df is None or df.empty:
        print(f"{csv_path} is missing or empty -- nothing to clean or upload.")
        return

    existing_cols = [c for c in COLUMNS_TO_DROP if c in df.columns]
    if existing_cols:
        df = df.drop(columns=existing_cols)
        print(f"  Dropped columns: {existing_cols}")

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Riyadh",
        )
        page = context.new_page()
        try:
            cat0_name_l1, cat0_name_ar, cat0_slug, sheets, records = clean_and_group(df, page=page, dt=dt)
        finally:
            browser.close()

    if not cat0_name_l1:
        print(f"No usable category data found in {csv_path}")
        return

    print(f"Category: {cat0_name_l1} ({cat0_slug}) -- {len(sheets)} sheet(s), {len(records)} ad(s)")
    for name, rows in sheets.items():
        print(f"  - {name}: {len(rows)}")

    if cat0_slug == "vehicles":
        manufacturer_groups, other_sheets = split_vehicle_records(records)

        for slug, group in manufacturer_groups.items():
            by_make = group_by_make_model(group["records"])
            upload_vehicles_by_manufacturer(by_make, cat0_name_l1, group["name"], dt)

        if other_sheets:
            excel_buf = build_excel(other_sheets)
            excel_key = upload_buffer(
                excel_buf,
                filename="Vehicles.xlsx",
                category_display=cat0_name_l1,
                file_type="excel",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                dt=dt,
            )
            print(f"Vehicles (other subcats) Excel -> {excel_key}")

            json_bytes = json.dumps(other_sheets, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            json_key = upload_buffer(
                io.BytesIO(json_bytes),
                filename="Vehicles.json",
                category_display=cat0_name_l1,
                file_type="json",
                content_type="application/json",
                dt=dt,
            )
            print(f"Vehicles (other subcats) JSON  -> {json_key}")

    elif has_any_subsubcategory(records):
        # Category has sub-sub-categories somewhere -- one file per
        # subcategory, sheets inside = sub-subcategories.
        subcat_files = build_subcategory_files(records)
        upload_subcategory_files(subcat_files, cat0_name_l1, dt)

    else:
        # Flat category (subcategories only, no sub-subcategories anywhere) --
        # unchanged: one combined file, one sheet per subcategory.
        excel_buf = build_excel(sheets)
        excel_key = upload_buffer(
            excel_buf,
            filename=f"{cat0_slug}.xlsx",
            category_display=cat0_name_l1,
            file_type="excel",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            dt=dt,
        )
        print(f"Excel -> {excel_key}")

        json_bytes = json.dumps(sheets, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        json_key = upload_buffer(
            io.BytesIO(json_bytes),
            filename=f"{cat0_slug}.json",
            category_display=cat0_name_l1,
            file_type="json",
            content_type="application/json",
            dt=dt,
        )
        print(f"JSON  -> {json_key}")

    summary = build_category_summary(records, cat0_name_l1, dt)
    summary_bytes = json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8")
    summary_key = upload_buffer(
        io.BytesIO(summary_bytes),
        filename="summary.json",
        category_display=cat0_name_l1,
        file_type="summary",
        content_type="application/json",
        dt=dt,
    )
    print(f"Summary -> {summary_key} ({summary['total_subcategories']} subcats, {summary['total_listings']} listings)")

    failed_matches = glob.glob("failed_pages_*.json")
    if failed_matches:
        with open(failed_matches[0], "r", encoding="utf-8") as f:
            failed_data = json.load(f)

        total_failed = failed_data.get("total_failed", 0)
        requests_total = 0
        stats_matches = glob.glob(f"request_stats_{cat0_slug}.json") or glob.glob("request_stats_*.json")
        if stats_matches:
            with open(stats_matches[0], "r", encoding="utf-8") as f:
                stats_data = json.load(f)
            requests_total = stats_data.get("per_source", {}).get("scraping_pages", 0)

        failed_data["error_rate_pct"] = (
            round(total_failed / requests_total * 100, 2) if requests_total > 0 else 0
        )

        failed_bytes = json.dumps(failed_data, ensure_ascii=False, indent=2).encode("utf-8")
        failed_key = upload_buffer(
            io.BytesIO(failed_bytes),
            filename="failed.json",
            category_display=cat0_name_l1,
            file_type="summary",
            content_type="application/json",
            dt=dt,
        )
        print(f"Failed -> {failed_key} ({failed_data['total_failed']} failed, {failed_data['error_rate_pct']}% error rate)")
    else:
        print("No failed_pages_*.json found -- skipping failed.json upload.")

    monitor_entry = {
        "name": cat0_name_ar or cat0_name_l1 or "Unknown",
        "slug": cat0_slug,
        "total_ads": len(records),
    }
    with open(f"monitor_entry_{cat0_slug}.json", "w", encoding="utf-8") as f:
        json.dump(monitor_entry, f, ensure_ascii=False, indent=2)
    print(f"Monitor entry -> monitor_entry_{cat0_slug}.json ({monitor_entry['total_ads']} ads)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean a raw Dubizzle KSA category CSV and push it to R2")
    parser.add_argument("csv_path", help="Path to the raw scraped CSV for one top-level category")
    args = parser.parse_args()
    run(args.csv_path)