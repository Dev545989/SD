"""import requests
import pandas as pd

BASE_URL = "https://content.dubizzle.sa/api/new-cars/all-new-cars"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.dubizzle.sa",
    "referer": "https://www.dubizzle.sa/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}

all_cars = []

# First page
response = requests.get(
    BASE_URL,
    params={"page": 1},
    headers=HEADERS,
    timeout=30,
)
response.raise_for_status()

data = response.json()

total_pages = data["page_info"]["total_pages"]
print(f"Total pages: {total_pages}")

all_cars.extend(data["cars"])

# Remaining pages
total_pages = 3
for page in range(2, total_pages + 1):
    print(f"Fetching page {page}/{total_pages}")

    response = requests.get(
        BASE_URL,
        params={"page": page},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    all_cars.extend(data["cars"])

print(f"Total cars collected: {len(all_cars)}")

# Save to CSV
df = pd.DataFrame(all_cars)
print(df.columns.tolist())
df.to_csv("new_cars.csv", index=False, encoding="utf-8-sig")

print("Saved to new_cars.csv")"""

import requests
import pandas as pd
import json

brand = "volkswagen"
model = "golf"
variant = "r-sel-2-door"

url = f"https://content.dubizzle.sa/api/new-cars/{brand}/{model}/{variant}"

headers = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.dubizzle.sa",
    "referer": "https://www.dubizzle.sa/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}

response = requests.get(url, headers=headers, timeout=30)
response.raise_for_status()

data = response.json()
print(data['data'].keys())

# Flatten the JSON
df = pd.json_normalize(data)

# Save to CSV
df.to_csv("car_details.csv", index=False, encoding="utf-8-sig")

with open('car_data_complete.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Saved to car_details.csv")


import requests
import pandas as pd
import json

brand = "volkswagen"
model = "golf"
variant = "r-sel-2-door"

url = f"https://content.dubizzle.sa/api/new-cars/{brand}/{model}/{variant}"

headers = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.dubizzle.sa",
    "referer": "https://www.dubizzle.sa/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}

response = requests.get(url, headers=headers, timeout=30)
response.raise_for_status()

data = response.json()
car_data = data.get('data', {})

# Get all keys from car_data
all_keys = list(car_data.keys())
print("Keys found:", all_keys)
print(f"Total columns: {len(all_keys)}")

# Create a dictionary with all keys as columns
row_data = {}

for key in all_keys:
    value = car_data.get(key)
    
    # Check the type of value
    if value is None:
        row_data[key] = ''
    elif isinstance(value, (str, int, float, bool)):
        # Simple values - keep as is
        row_data[key] = value
    elif isinstance(value, (dict, list)):
        # Complex values - convert to JSON string
        row_data[key] = json.dumps(value, ensure_ascii=False)
    else:
        row_data[key] = str(value)

# Create DataFrame with one row
df = pd.DataFrame([row_data])

# Save to CSV
df.to_csv('car_data_complete_columns.csv', index=False, encoding='utf-8-sig')

print(f"\n✅ Saved: car_data_complete_columns.csv")
print(f"📊 Total columns: {len(df.columns)}")
print(f"📋 All columns: {', '.join(df.columns)}")