import json
import time
import requests
import pandas as pd

URL = "https://search.mena.sector.run/_msearch"

AUTHORIZATION ="Basic b2x4LXNhLXByb2R1Y3Rpb24tc2VhcmNoOkhqOSFyOTM1KUtBQ1dpcT5KKytLV0VFWX1ucTc1SH1B"

INDEX = "olx-sa-production-ads-ar"
CATEGORY_SLUG = "vehicles"
CATEGORY_SLUG = "mobile-phones-accessories"
CATEGORY_SLUG = "electronics-home-appliances"
CATEGORY_SLUG = "home-garden"
CATEGORY_SLUG = "fashion-beauty"
CATEGORY_SLUG = "pets"
CATEGORY_SLUG = "kids-babies"
CATEGORY_SLUG = "sporting-goods-bikes"
CATEGORY_SLUG = "hobbies-music-art-books"
CATEGORY_SLUG = "jobs-services"
CATEGORY_SLUG = "business-industrial"
CATEGORY_SLUG = "vehicles"

LOCATION_ID = "0-1"

PAGE_SIZE = 100
MAX_RETRIES = 10


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
    )
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

    payload = (
        json.dumps({"index": INDEX})
        + "\n"
        + json.dumps(query)
        + "\n"
    )

    for attempt in range(MAX_RETRIES):

        try:

            response = requests.post(
                URL,
                params=params,
                headers=headers,
                data=payload,
                timeout=60
            )

            if response.status_code in [429, 500, 502, 503, 504]:
                raise Exception(f"HTTP {response.status_code}")

            response.raise_for_status()

            return response.json()

        except Exception as e:

            print(
                f"Attempt {attempt + 1}/{MAX_RETRIES}: {e}"
            )

            if attempt == MAX_RETRIES - 1:
                raise

            wait = (attempt + 1) * 5

            print(f"Retrying in {wait} seconds...")

            time.sleep(wait)

def build_query(search_after=None, product=None):

    must = [
        {"term": {"category.slug": CATEGORY_SLUG}},
        {"term": {"location.externalID": LOCATION_ID}},
    ]

    must_not = []

    if product is None:
        must_not.append(
            {
                "terms": {
                    "product": [
                        "featured",
                        "elite"
                    ]
                }
            }
        )
    else:
        must.append(
            {
                "term": {
                    "product": product
                }
            }
        )

    query = {
        "size": PAGE_SIZE,
        "track_total_hits": 200000,
        "query": {
            "bool": {
                "must": must,
                "must_not": must_not
            }
        },
        "sort": [
            {
                "timestamp": {
                    "order": "desc"
                }
            },
            {
                "id": {
                    "order": "desc"
                }
            }
        ],
        "timeout": "5s"
    }

    if search_after is not None:
        query["search_after"] = search_after

    return query

def scrape(product=None):

    title = "NORMAL" if product is None else product.upper()

    print(f"\n========== {title} ==========")

    all_records = []
    search_after = None

    while True:

        query = build_query(
            search_after=search_after,
            product=product
        )

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

        print(
            f"{title}: {len(hits)} | "
            f"Collected={len(all_records)} | "
            f"Total={total}"
        )

        if not hits:
            if total == 0:
                print(f"No {title.lower()} ads found.")
            else:
                print(json.dumps(response, indent=2))
            break

        all_records.extend(
            hit["_source"]
            for hit in hits
        )

        if len(hits) < PAGE_SIZE:
            break

        search_after = hits[-1]["sort"]

        time.sleep(2)

    print(f"{title} Total = {len(all_records)}")

    return all_records


normal = scrape()

featured = scrape("featured")

elite = scrape("elite")

all_records = normal + featured + elite

df = pd.DataFrame(all_records)

print("Before dedup:", len(df))

if "id" in df.columns:
    df = df.drop_duplicates(
        subset=["id"],
        keep="first"
    )

print("After dedup:", len(df))

df.to_csv(
    "dubizzle_vehicles.csv",
    index=False,
    encoding="utf-8-sig"
)

print("Done!")