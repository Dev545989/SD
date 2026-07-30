import argparse
import glob
import io
import json
import os
import re
from datetime import datetime, timezone

import pandas as pd
import requests as req
from PIL import Image
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

from r2_uploader import upload_buffer

COLUMNS_TO_DROP: list[str] = ['content', 'description', 'content_l1', 'description_l1', 'seo_links',
                              'mapped_model_id', 'mapped_make_id']


def clean_text(value) -> str:
    if value is None:
        return "Unknown"
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or "Unknown"


def sanitize_name(value) -> str:
    text = clean_text(value)
    text = re.sub(r'[\\/:*?"<>|]', "-", text)
    return text or "Unknown"


def find_col(df: pd.DataFrame, name: str) -> str | None:
    """Matches an exact column name or a flattened one like overview_data.<name>."""
    if name in df.columns:
        return name
    for c in df.columns:
        if c == name or c.endswith(f".{name}"):
            return c
    return None


def parse_images_field(value) -> list[dict]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def extract_image_urls(images_field) -> list[str]:
    """overview_data.images -> full-size 'url' for each image entry (not the small thumbnail)."""
    items = parse_images_field(images_field)
    urls = []
    for item in items:
        if isinstance(item, dict) and item.get("url"):
            urls.append(item["url"])
    return urls


def download_and_upload_images(urls: list[str], car_ref: str, dt: datetime) -> list[str]:
    r2_paths = []
    uploaded = 0
    failed = 0

    if not urls:
        return r2_paths

    prefix = car_ref or "unknown"

    for idx, img_url in enumerate(urls, start=1):
        filename = f"{prefix}-{idx}.webp"
        try:
            r = req.get(img_url, timeout=15)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=100, method=6)
                buf.seek(0)

                r2_key = upload_buffer(buf, filename=filename, category_display= 'motors', file_type=file_type,
                                                        content_type="image/webp", dt=dt)
                if r2_key:
                    r2_paths.append(r2_key)
                    uploaded += 1
                else:
                    failed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"    [ERROR] {filename}: {e}")
            failed += 1

    if uploaded or failed:
        print(f"    {prefix}: {uploaded} uploaded, {failed} failed out of {len(urls)}")

    return r2_paths



def load_raw(input_path: str) -> pd.DataFrame | None:
    if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
        return None
    if input_path.endswith(".json"):
        return pd.read_json(input_path)
    return pd.read_csv(input_path)


def clean_and_split(df: pd.DataFrame, dt: datetime) -> dict[str, dict[str, list]]:
    images_col = find_col(df, "images")
    make_col = find_col(df, "make_slug")
    model_col = find_col(df, "model_slug")
    version_col = find_col(df, "version_id")

    if make_col is None or model_col is None:
        raise ValueError("Could not find make_slug / model_slug columns in the data.")

    by_make: dict[str, dict[str, list]] = {}

    for _, row in df.iterrows():
        record = row.to_dict()

        raw_images = record.pop(images_col, None) if images_col else None
        urls = extract_image_urls(raw_images)

        make_slug = sanitize_name(record.get(make_col) or "unknown")
        model_slug = sanitize_name(record.get(model_col) or "unknown")
        car_ref = str(record.get(version_col) or "")

        r2_paths = download_and_upload_images(urls, car_ref, dt)
        record["images"] = r2_paths

        by_make.setdefault(make_slug, {}).setdefault(model_slug, []).append(record)

    return by_make


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


def _stringify_complex_columns(sheet_df: pd.DataFrame) -> pd.DataFrame:
    for col in sheet_df.columns:
        sheet_df[col] = sheet_df[col].apply(
            lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
        )
    return sheet_df


def build_excel(models: dict[str, list]) -> io.BytesIO:
    wb = Workbook()
    wb.remove(wb.active)
    used_names: set = set()

    for model_slug, rows in models.items():
        ws = wb.create_sheet(title=safe_sheet_name(model_slug, used_names))
        sheet_df = _stringify_complex_columns(pd.DataFrame(rows))
        for r in dataframe_to_rows(sheet_df, index=False, header=True):
            ws.append(r)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def upload_by_make(by_make: dict[str, dict[str, list]], dt: datetime) -> None:
    for make_slug, models in by_make.items():
        total_ads = sum(len(rows) for rows in models.values())
        print(f"  - {make_slug}: {len(models)} model(s), {total_ads} car(s)")

        excel_buf = build_excel(models)
        excel_key = upload_buffer(
            excel_buf, filename=f"{make_slug}.xlsx", category_display= 'motors', file_type="excel",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            dt=dt,
        )
        print(f"      Excel -> {excel_key}")

        json_bytes = json.dumps(models, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        json_key = upload_buffer(
            io.BytesIO(json_bytes), filename=f"{make_slug}.json", category_display= 'motors', file_type="json",
            content_type="application/json", dt=dt,
        )
        print(f"      JSON  -> {json_key}")


def build_summary(by_make: dict[str, dict[str, list]], dt: datetime) -> dict:
    subcategories = []
    total_listings = 0

    for make_slug, models in by_make.items():
        listings_count = sum(len(rows) for rows in models.values())
        total_listings += listings_count
        model_names = sorted(models.keys())

        subcategories.append({
            "name_ar": "",
            "name_en": make_slug,
            "slug": make_slug,
            "listings_count": listings_count,
            "has_subcategories": len(model_names) > 1,
            "subcategories": model_names,
        })

    return {
        "scraped_at": dt.isoformat(),
        "data_scraped_date": dt.strftime("%Y-%m-%d"),
        "saved_to_R2_date": dt.strftime("%Y-%m-%d"),
        "total_subcategories": len(subcategories),
        "total_listings": total_listings,
        "subcategories": subcategories,
    }


def run(input_path: str):
    dt = datetime.now(timezone.utc)
    df = load_raw(input_path)

    if df is None or df.empty:
        print(f"{input_path} is missing or empty -- nothing to clean or upload.")
        return

    existing_cols = [c for c in COLUMNS_TO_DROP if c in df.columns]
    if existing_cols:
        df = df.drop(columns=existing_cols)
        print(f"  Dropped columns: {existing_cols}")

    by_make = clean_and_split(df, dt)
    print(f"Split into {len(by_make)} make(s)")

    upload_by_make(by_make, dt)

    summary = build_summary(by_make, dt)
    summary_bytes = json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8")
    summary_key = upload_buffer(
        io.BytesIO(summary_bytes), filename="summary.json", category_display= 'motors', file_type="summary",
        content_type="application/json", dt=dt,
    )
    print(f"Summary -> {summary_key} ({summary['total_subcategories']} makes, {summary['total_listings']} cars)")

    failed_matches = glob.glob("failed_urls_motors.json")
    stats_matches = glob.glob("request_stats_motors.json")

    if failed_matches:
        with open(failed_matches[0], "r", encoding="utf-8") as f:
            failed_data = json.load(f)

        requests_total = 0
        if stats_matches:
            with open(stats_matches[0], "r", encoding="utf-8") as f:
                stats_data = json.load(f)
            requests_total = stats_data.get("per_source", {}).get("car_details", 0)

        total_failed = failed_data.get("total_failed", 0)
        failed_data["error_rate_pct"] = (
            round(total_failed / requests_total * 100, 2) if requests_total > 0 else 0
        )

        failed_bytes = json.dumps(failed_data, ensure_ascii=False, indent=2).encode("utf-8")
        failed_key = upload_buffer(
            io.BytesIO(failed_bytes), filename="failed.json", category_display= 'motors', file_type="summary",
            content_type="application/json", dt=dt,
        )
        print(f"Failed -> {failed_key} ({total_failed} failed, {failed_data['error_rate_pct']}% error rate)")
    else:
        print("No failed_urls_motors.json found -- skipping failed.json upload.")

    if stats_matches:
        with open(stats_matches[0], "r", encoding="utf-8") as f:
            stats_data = json.load(f)

        metrics = {
            "requests_total": stats_data.get("total_requests", 0),
            "requests_per_min": stats_data.get("total_req_per_min", 0),
            "duration_sec": round((stats_data.get("total_duration_min", 0) or 0) * 60, 2),
        }
        metrics_bytes = json.dumps(metrics, ensure_ascii=False, indent=2).encode("utf-8")
        metrics_key = upload_buffer(
            io.BytesIO(metrics_bytes), filename="request_metrics.json", category_display= 'motors', file_type="summary",
            content_type="application/json", dt=dt,
        )
        print(f"Metrics -> {metrics_key} ({metrics['requests_total']} req, {metrics['requests_per_min']} req/min)")
    else:
        print("No request_stats_motors.json found -- skipping request_metrics.json upload.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean and upload Dubizzle KSA motors (new cars) data.")
    parser.add_argument("input_path", help="Path to all_motors_cars.csv or .json produced by main.py")
    args = parser.parse_args()
    run(args.input_path)