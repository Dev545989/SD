import argparse
import json
import os
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests

URL = "https://search.mena.sector.run/_msearch"
AUTHORIZATION = os.getenv("DUBIZZLE_SA_AUTH", "")
INDEX = "olx-sa-production-ads-ar"
LOCATION_ID = "0-1"

PAGE_SIZE = 100
MAX_RETRIES = 10

TARGET_DATE = (datetime.now(timezone.utc) - timedelta(days=1)).date()

CATEGORY_SLUGS = [
    "vehicles",
    "mobile-phones-accessories",
    "electronics-home-appliances",
    "home-garden",
    "fashion-beauty",
    "pets",
    "kids-babies",
    "sporting-goods-bikes",
    "hobbies-music-art-books",
    "jobs-services",
    "business-industrial",
]

headers = {
    "accept": "*/*",
    "authorization": AUTHORIZATION,
    "content-type": "application/x-ndjson",
    "origin": "https://www.dubizzle.sa",
    "referer": "https://www.dubizzle.sa/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}

params = {
    "filter_path": (
        "took,"
        "*.hits.total.*,"
        "*.hits.hits._source.*,"
        "*.hits.hits.sort,"
        "*.error"
    )
}


def send_query(query):
    payload = json.dumps({"index": INDEX}) + "\n" + json.dumps(query) + "\n"

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(URL, params=params, headers=headers, data=payload, timeout=60)

            if response.status_code in [429, 500, 502, 503, 504]:
                raise Exception(f"HTTP {response.status_code}")

            response.raise_for_status()
            return response.json()

        except Exception as e:
            print(f"Attempt {attempt + 1}/{MAX_RETRIES}: {e}")
            if attempt == MAX_RETRIES - 1:
                raise
            wait = (attempt + 1) * 5
            print(f"Retrying in {wait} seconds...")
            time.sleep(wait)


def build_query(category_slug, search_after=None, product=None):
    must = [
        {"term": {"category.slug": category_slug}},
        {"term": {"location.externalID": LOCATION_ID}},
    ]
    must_not = []

    if product is None:
        must_not.append({"terms": {"product": ["featured", "elite"]}})
    else:
        must.append({"term": {"product": product}})

    query = {
        "size": PAGE_SIZE,
        "track_total_hits": 200000,
        "query": {"bool": {"must": must, "must_not": must_not}},
        "sort": [{"timestamp": {"order": "desc"}}, {"id": {"order": "desc"}}],
        "timeout": "5s",
    }

    if search_after is not None:
        query["search_after"] = search_after

    return query


def scrape(category_slug, product=None):
    title = "NORMAL" if product is None else product.upper()
    print(f"\n========== {category_slug} / {title} ==========")

    all_records = []
    search_after = None

    while True:
        query = build_query(category_slug, search_after=search_after, product=product)
        data = send_query(query)
        responses = data.get("responses", [])

        if not responses:
            print(json.dumps(data, indent=2))
            break

        response = responses[0]
        if "error" in response:
            print(json.dumps(response["error"], indent=2))
            return []

        hits_obj = response.get("hits", {})
        total = hits_obj.get("total", {}).get("value", 0)
        hits = hits_obj.get("hits", [])

        print(f"{title}: {len(hits)} | Collected={len(all_records)} | Total={total}")

        if not hits:
            if total == 0:
                print(f"No {title.lower()} ads found.")
            else:
                print(json.dumps(response, indent=2))
            break

        all_records.extend(hit["_source"] for hit in hits)

        if len(hits) < PAGE_SIZE:
            break

        search_after = hits[-1]["sort"]
        time.sleep(2)

    print(f"{title} Total = {len(all_records)}")
    return all_records


def filter_yesterday_hits(hits):
    filtered = []

    for hit in hits:
        created_at = hit.get("createdAt")

        if created_at is None:
            continue

        try:
            dt = datetime.fromtimestamp(float(created_at), tz=timezone.utc)

            if dt.date() == TARGET_DATE:
                filtered.append(hit)

        except (ValueError, TypeError):
            pass

    return filtered


def run(category_slug: str, out_dir: str = "."):
    normal = scrape(category_slug)
    featured = scrape(category_slug, "featured")
    elite = scrape(category_slug, "elite")

    all_records = normal + featured + elite
    print(f"Before yesterday filter ({TARGET_DATE}):", len(all_records))
    all_records = filter_yesterday_hits(all_records)
    print("After yesterday filter:", len(all_records))

    df = pd.DataFrame(all_records)

    print("Before dedup:", len(df))
    if "id" in df.columns:
        df = df.drop_duplicates(subset=["id"], keep="first")
    print("After dedup:", len(df))

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{category_slug}.csv")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"Done! -> {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--category", required=True, choices=CATEGORY_SLUGS,
        help="Top-level category slug to scrape",
    )
    parser.add_argument("--out-dir", default="data/raw")
    args = parser.parse_args()
    run(args.category, args.out_dir)