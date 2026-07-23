import argparse
import ast
import io
import json
import re
from datetime import datetime

import pandas as pd
import requests as req
from PIL import Image
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

from r2_uploader import upload_buffer

THUMB_URL_TEMPLATE = "https://images.dubizzle.sa/thumbnails/{photo_id}-800x600.webp"


# ---------------------------------------------------------------------------
# Category parsing
# ---------------------------------------------------------------------------

def parse_category(cat_field):
    """
    `category` comes back from Elasticsearch as a list of dicts, one per level
    (0 = top category, 1 = subcategory, 2 = sub-subcategory -- level 2 not always present).
    When read back from a CSV it arrives as a stringified list, so handle both.
    Returns (cat0, cat1, cat2) -- any of them can be None.
    """
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
    """
    One sheet per subcategory (level 1), e.g. "Other Business & Industrial".
    If a level-2 sub-subcategory exists too, it's appended, e.g.
    "Decoration - Accessories (Art - Paintings)".

    Note: Excel sheet names can't contain [ ] : \\ / ? * -- so square brackets
    are swapped for parentheses rather than dropped.
    """
    if cat1 is None:
        name = "Uncategorized"
    else:
        name = cat1.get("name_l1") or cat1.get("name") or "Uncategorized"
        if cat2:
            sub = cat2.get("name_l1") or cat2.get("name")
            if sub:
                name = f"{name} ({sub})"

    name = re.sub(r"[:\\/?*\[\]]", "-", name)
    return name[:31] or "Uncategorized"


# ---------------------------------------------------------------------------
# Images: extract URLs, download, convert to WEBP, upload to R2
# ---------------------------------------------------------------------------

def photo_urls(photos_field) -> list:
    """
    `photos` is a list of dicts like {'id': 3004387, 'externalID': ..., 'orderIndex': 0, ...}.
    Build the real image URL from each photo's numeric `id`.
    Handles the field arriving as a real list or as a stringified list from a CSV.
    """
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


def download_images(images: list, id_prod: str, category_display: str, dt: datetime = None) -> list:
    """
    Downloads each image, converts to WEBP, and uploads to R2 under
    DKSA/year=.../month=.../day=.../{category_display}/images/{id_prod}-{n}.webp
    """
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


# ---------------------------------------------------------------------------
# Clean, group by category, build Excel/JSON, upload
# ---------------------------------------------------------------------------

def load_raw(csv_path: str) -> pd.DataFrame:
    return pd.read_csv(csv_path)


def clean_and_group(df: pd.DataFrame, dt: datetime = None):
    """
    One input CSV == one top-level category (that's how the scraper's --category works).
    Returns (cat0_name_l1, cat0_slug, sheets, all_records) where `sheets` maps
    sheet name -> list of row dicts, and `all_records` is the flat list for the JSON dump.
    """
    sheets: dict[str, list] = {}
    all_records = []
    cat0_name_l1 = None
    cat0_slug = None

    for _, row in df.iterrows():
        cat0, cat1, cat2 = parse_category(row.get("category"))
        if cat0 is None:
            continue

        if cat0_name_l1 is None:
            cat0_name_l1 = cat0.get("name_l1")
            cat0_slug = cat0.get("slug")

        sheet = sheet_name_for(cat1, cat2)

        urls = photo_urls(row.get("photos"))
        ad_id = str(row.get("id") or row.get("externalID") or "")

        image_r2_paths = download_images(urls, id_prod=ad_id, category_display=cat0_name_l1, dt=dt)

        record = row.to_dict()
        record["image_r2_paths"] = image_r2_paths

        sheets.setdefault(sheet, []).append(record)
        all_records.append(record)

    return cat0_name_l1, cat0_slug, sheets, all_records


def _stringify_complex_columns(sheet_df: pd.DataFrame) -> pd.DataFrame:
    for col in sheet_df.columns:
        sheet_df[col] = sheet_df[col].apply(
            lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
        )
    return sheet_df


def build_excel(sheets: dict) -> io.BytesIO:
    wb = Workbook()
    wb.remove(wb.active)

    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        sheet_df = _stringify_complex_columns(pd.DataFrame(rows))
        for r in dataframe_to_rows(sheet_df, index=False, header=True):
            ws.append(r)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def run(csv_path: str):
    dt = datetime.now()  # single timestamp shared by every upload in this run
    df = load_raw(csv_path)
    cat0_name_l1, cat0_slug, sheets, records = clean_and_group(df, dt=dt)

    if not cat0_name_l1:
        print(f"No usable category data found in {csv_path}")
        return

    print(f"Category: {cat0_name_l1} ({cat0_slug}) -- {len(sheets)} sheet(s), {len(records)} ad(s)")
    for name, rows in sheets.items():
        print(f"  - {name}: {len(rows)}")

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

    json_bytes = json.dumps(records, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    json_key = upload_buffer(
        io.BytesIO(json_bytes),
        filename=f"{cat0_slug}.json",
        category_display=cat0_name_l1,
        file_type="json",
        content_type="application/json",
        dt=dt,
    )
    print(f"JSON  -> {json_key}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean a raw Dubizzle KSA category CSV and push it to R2")
    parser.add_argument("csv_path", help="Path to the raw scraped CSV for one top-level category")
    args = parser.parse_args()
    run(args.csv_path)