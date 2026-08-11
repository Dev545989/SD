"""

# get_columns.py
# ==============
# Get columns from each category - simple version.

import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

# ===== Settings =====
URL = "https://search.mena.sector.run/_msearch"
AUTHORIZATION = os.getenv("AUTHORIZATION")
INDEX = "olx-sa-production-ads-ar"
LOCATION_ID = "0-1"

# Your categories
CATEGORIES = [
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
    "authorization": AUTHORIZATION,
    "content-type": "application/x-ndjson",
}

# ===== Simple functions =====

def fetch_ads(category_slug):
    # Get first 5 ads from a category
    
    # 1. Build query
    query = {
        "size": 5,  # Only 5!
        "query": {
            "bool": {
                "must": [
                    {"term": {"category.slug": category_slug}},
                    {"term": {"location.externalID": LOCATION_ID}},
                ]
            }
        },
        "sort": [{"timestamp": {"order": "desc"}}],
    }
    
    # 2. Format payload
    payload = json.dumps({"index": INDEX}) + "\n" + json.dumps(query) + "\n"
    
    # 3. Send request
    response = requests.post(URL, headers=headers, data=payload, timeout=30)
    data = response.json()
    
    # 4. Extract ads
    hits = data.get("responses", [{}])[0].get("hits", {}).get("hits", [])
    
    # 5. Get _source from each ad
    records = []
    for hit in hits:
        source = hit.get("_source", {})
        if source:
            records.append(source)
    
    return records

def get_columns(records):
    columns = set()
    
    for record in records:
        for key in record.keys():
            columns.add(key)
    
    return sorted(list(columns))

# ===== Run =====

print("=" * 60)
print("Get columns from each category")
print("=" * 60)

all_results = {}

for category in CATEGORIES:
    print(f"\n📂 {category}...")
    
    try:
        # Get 5 ads only
        records = fetch_ads(category)
        
        # Get columns
        columns = get_columns(records)
        
        # Save
        all_results[category] = {
            "count": len(records),
            "columns": columns
        }
        
        print(f"   ✅ {len(records)} ads, {len(columns)} columns")
        print(f"   📝 Columns: {', '.join(columns)}")
        
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        all_results[category] = {
            "count": 0,
            "columns": [],
            "error": str(e)
        }

# ===== Save results =====

with open("columns.json", "w", encoding="utf-8") as f:
    json.dump(all_results, f, ensure_ascii=False, indent=2)

print("\n" + "=" * 60)
print(f"✅ Done! Results saved to columns.json")
print("=" * 60)

# ===== Print summary =====
print("\n📊 Summary:")
for category, data in all_results.items():
    print(f"  {category}: {len(data['columns'])} columns")"""

import json
from typing import Dict, List, Set

def analyze_columns(columns_data: Dict[str, Dict]) -> Dict:
    """
    Analyze columns across all categories.
    Returns for each column: which categories have it and which don't.
    """
    # Get all unique columns
    all_columns = set()
    category_columns = {}
    
    for category, info in columns_data.items():
        cols = set(info.get("columns", []))
        category_columns[category] = cols
        all_columns.update(cols)
    
    # Analyze each column
    result = {}
    
    for col in sorted(all_columns):
        present_in = []
        missing_in = []
        
        for category, cols in category_columns.items():
            if col in cols:
                present_in.append(category)
            else:
                missing_in.append(category)
        
        result[col] = {
            "present_in": present_in,
            "missing_in": missing_in,
            "total_present": len(present_in),
            "total_missing": len(missing_in),
            "is_standard": len(missing_in) == 0
        }
    
    return result


def print_column_analysis(analysis: Dict):
    """Print column analysis in a readable format."""
    
    # 1. Standard columns (present in ALL categories)
    standard = {col: data for col, data in analysis.items() if data["is_standard"]}
    
    print("=" * 80)
    print(f"📊 STANDARD COLUMNS (present in ALL {len(standard)} categories)")
    print("=" * 80)
    for col in sorted(standard.keys()):
        print(f"  ✅ {col}")
    print(f"\nTotal: {len(standard)} columns\n")
    
    # 2. Columns missing from some categories
    partial = {col: data for col, data in analysis.items() if not data["is_standard"]}
    
    print("=" * 80)
    print(f"📊 PARTIAL COLUMNS (missing from some categories) - {len(partial)} columns")
    print("=" * 80)
    
    # Sort by number of missing (most missing first)
    sorted_partial = sorted(partial.items(), key=lambda x: x[1]["total_missing"], reverse=True)
    
    for col, data in sorted_partial:
        present = ", ".join(data["present_in"])
        missing = ", ".join(data["missing_in"])
        print(f"\n  📌 {col}")
        print(f"     ✅ Present in: {present}")
        print(f"     ❌ Missing in: {missing}")
        print(f"     📊 {data['total_present']}/{data['total_present'] + data['total_missing']} categories")
    
    # 3. Summary
    print("\n" + "=" * 80)
    print("📊 SUMMARY")
    print("=" * 80)
    print(f"  Total unique columns: {len(analysis)}")
    print(f"  Standard columns (in all): {len(standard)}")
    print(f"  Partial columns (missing some): {len(partial)}")
    
    # 4. Columns only in one category
    unique_cols = {col: data for col, data in analysis.items() if data["total_present"] == 1}
    if unique_cols:
        print(f"\n  🔸 Columns only in ONE category ({len(unique_cols)}):")
        for col, data in unique_cols.items():
            print(f"     • {col} → only in {data['present_in'][0]}")


# Run it
with open("columns.json", "r", encoding="utf-8") as f:
    columns_data = json.load(f)

analysis = analyze_columns(columns_data)
print_column_analysis(analysis)