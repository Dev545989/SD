import random
import re
from text_utils import clean_text
from request_tracker import tracker

AD_URL_TEMPLATE = "https://www.dubizzle.sa/en/ad/{slug}-ID{externalID}.html"

CONTACT_BUTTON_SELECTORS = [
    'button:has-text("Show phone number")',
    'button:has-text("Show Phone Number")',
    'button:has-text("Show Number")',
    'button:has-text("Call")',
    'button:has-text("اتصل")',
    'button:has-text("عرض")',
    'button:has-text("Phone")',
    '[data-testid*="phone" i]',
    '[data-testid*="show-phone" i]',
    '[data-testid="call-cta-button"]',
    'button[class*="phone"]',
    'a[class*="phone"]',
    '[class*="contact"] button',
    '[class*="contact"] a',
]

EMPTY_CONTACT_INFO = {}


def build_ad_url(record: dict) -> str | None:
    ad_id = record.get("externalID")
    slug = record.get("slug")
    if not ad_id or not slug:
        return None
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", clean_text(slug)).strip("-").lower()
    return AD_URL_TEMPLATE.format(slug=slug or "ad", externalID=ad_id)


def _call_api_directly(page, listing_id: str, ad_url: str):
    api_url = f"https://www.dubizzle.sa/api/listing/{listing_id}/contactInfo/"
    try:
        resp = page.request.get(
            api_url,
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": ad_url,
            },
            timeout=10000,
        )
        if resp.status == 200:
            return resp.json()
    except Exception as e:
        print(f"      [API-DIRECT] Failed: {e}")
    return None


def _try_fetch_once(page, ad_url: str, listing_id: str):
    page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
    tracker.log_request(source="scraping_phone_num")
    page.wait_for_timeout(random.uniform(1500, 2500))

    data = _call_api_directly(page, listing_id, ad_url)
    if data and data.get("name") is not None:
        return data

    call_button = None
    for selector in CONTACT_BUTTON_SELECTORS:
        loc = page.locator(selector).first
        try:
            if loc.is_visible(timeout=2000):
                call_button = loc
                break
        except Exception:
            continue

    if call_button is None:
        return {"_no_phone": True}

    call_button.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    call_button.click(force=True)
    page.wait_for_timeout(3000)

    return _call_api_directly(page, listing_id, ad_url)


def fetch_contact_info(page, ad_url: str, max_retries: int = 2) -> dict | None:
    match = re.search(r"ID(\d+)\.html", ad_url or "")
    if not match:
        print(f"  [PARSE-FAIL] {ad_url}")
        return None
    listing_id = match.group(1)

    for attempt in range(1, max_retries + 1):
        try:
            data = _try_fetch_once(page, ad_url, listing_id)
        except Exception as e:
            if "Timeout" in str(e) or "net::" in str(e):
                if attempt < max_retries:
                    wait = random.uniform(1, 3)
                    print(f"    [RETRY] network error (attempt {attempt}): {e}")
                    page.wait_for_timeout(wait * 1000)
                    continue
            print(f"  [NETWORK-FAIL] {ad_url} | {e}")
            return None

        if isinstance(data, dict) and data.get("_no_phone"):
            print(f"  [NO-BUTTON] {ad_url}")
            return None

        if isinstance(data, dict) and data.get("name") is not None:
            mobile = data.get("mobile") or data.get("whatsapp") or "N/A"
            #print(f"  [SUCCESS] {ad_url} | Name: {data['name']} | Mobile: {mobile}")
            print(f"  [SUCCESS] {ad_url}")

            return data

        if data is not None:
            print(f"  [EMPTY-API] {ad_url}")
            return None

        if attempt < max_retries:
            wait = random.uniform(1, 3)
            print(f"    [RETRY] empty response (attempt {attempt}), waiting {wait:.1f}s...")
            page.wait_for_timeout(wait * 1000)

    print(f"  [FAILED] {ad_url}")
    return None