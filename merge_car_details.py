import argparse
import glob
import json
import os

import pandas as pd


def merge_details(details_dir: str, output_csv: str):
    files = sorted(glob.glob(os.path.join(details_dir, "car_details_*.csv")))
    if not files:
        print(f"No car_details_*.csv files found under {details_dir}")
        return
    dfs = []
    for f in files:
        if os.path.getsize(f) == 0:
            continue
        df = pd.read_csv(f)
        if not df.empty:
            dfs.append(df)
    if not dfs:
        print("All chunk files were empty -- nothing to merge.")
        return
    merged = pd.concat(dfs, ignore_index=True)
    merged.insert(0, "_row_id", range(len(merged)))
    merged.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"Merged {len(files)} chunk file(s) -> {output_csv} ({len(merged)} rows)")


def merge_failed(details_dir: str, output_json: str):
    files = sorted(glob.glob(os.path.join(details_dir, "failed_urls_motors_*.json")))
    if not files:
        print(f"No failed_urls_motors_*.json files found under {details_dir}")
        return
    all_failed_urls = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        all_failed_urls.extend(data.get("failed_urls", []))
    with open(output_json, "w", encoding="utf-8") as fh:
        json.dump({
            "total_failed": len(all_failed_urls),
            "failed_urls": all_failed_urls,
        }, fh, ensure_ascii=False, indent=2)
    print(f"Merged {len(files)} failed-urls file(s) -> {output_json} ({len(all_failed_urls)} failed)")


def merge_stats(details_dir: str, output_json: str):
    files = sorted(glob.glob(os.path.join(details_dir, "request_stats_motors_*.json")))
    if not files:
        print(f"No request_stats_motors_*.json files found under {details_dir}")
        return

    total_requests = 0
    per_source = {}
    max_duration_min = 0.0
    total_duration_seconds = 0.0
    has_total_duration = False

    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            stats = json.load(fh)
        total_requests += stats.get("total_requests", 0)
        max_duration_min = max(max_duration_min, stats.get("total_duration_min", 0) or 0)
        
        if "total_duration" in stats:
            total_duration_seconds = max(total_duration_seconds, stats.get("total_duration", 0))
            has_total_duration = True
        
        for source, count in stats.get("per_source", {}).items():
            per_source[source] = per_source.get(source, 0) + count

    total_req_per_min = round(total_requests / max_duration_min, 2) if max_duration_min > 0 else total_requests

    merged = {
        "total_requests": total_requests,
        "total_duration_min": round(max_duration_min, 2),
        "total_req_per_min": total_req_per_min,
        "per_source": per_source,
    }
    
    if has_total_duration:
        merged["total_duration"] = total_duration_seconds

    with open(output_json, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    print(f"Merged {len(files)} stats file(s) -> {output_json} ({total_requests} total requests)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--details-dir", default="details_parts")
    parser.add_argument("--output-csv", default="all_motors_cars.csv")
    parser.add_argument("--failed-output", default="failed_urls_motors.json")
    parser.add_argument("--stats-output", default="request_stats_motors.json")
    args = parser.parse_args()

    merge_details(args.details_dir, args.output_csv)
    merge_failed(args.details_dir, args.failed_output)
    merge_stats(args.details_dir, args.stats_output)