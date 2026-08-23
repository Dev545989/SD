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
    'button[class*="call"]',
    'a[class*="call"]',
    '[data-testid="phone-number-button"]',
]

EMPTY_CONTACT_INFO = {}


def build_ad_url(record: dict) -> str | None:
    ad_id = record.get("externalID")
    slug = record.get("slug")
    if not ad_id or not slug:
        return None
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", clean_text(slug)).strip("-").lower()
    return AD_URL_TEMPLATE.format(slug=slug or "ad", externalID=ad_id)


def has_valid_phone(data: dict) -> bool:
    """Return True if data has a real phone number (not null/N/A/empty)."""
    if not isinstance(data, dict):
        return False
    for key in ("mobile", "whatsapp", "proxyMobile"):
        val = data.get(key)
        if val is None:
            continue
        val_str = str(val).strip()
        if val_str and val_str.lower() not in ("n/a", "null", "none", "nan", ""):
            digits = re.sub(r'\D', '', val_str)
            if len(digits) >= 7:
                return True

    mobile_numbers = data.get("mobileNumbers")
    if isinstance(mobile_numbers, list):
        for num in mobile_numbers:
            digits = re.sub(r'\D', '', str(num))
            if len(digits) >= 7:
                return True
    return False


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
    # ✅ networkidle + timeout أطول
    page.goto(ad_url, wait_until="networkidle", timeout=45000)
    tracker.log_request(source="scraping_phone_num")

    # ✅ انتظار أطول عشان الـ dynamic content
    page.wait_for_timeout(random.uniform(2500, 4500))

    # 1) Try API directly first
    data = _call_api_directly(page, listing_id, ad_url)
    if has_valid_phone(data):
        return data

    # 2) ✅ Human-like scroll عشان يظهر الزر لو كان lazy-loaded
    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
    page.wait_for_timeout(random.uniform(600, 1200))
    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.8)")
    page.wait_for_timeout(random.uniform(400, 800))
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(random.uniform(800, 1500))

    # 3) ✅ 3 محاولات للبحث عن الزر مع انتظار
    call_button = None
    for attempt in range(3):
        for selector in CONTACT_BUTTON_SELECTORS:
            loc = page.locator(selector).first
            try:
                loc.wait_for(state="visible", timeout=8000)
                call_button = loc
                break
            except Exception:
                continue

        if call_button:
            break

        if attempt < 2:
            page.wait_for_timeout(random.uniform(1500, 3000))
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)

    if call_button is None:
        return {"_no_phone": True}

    # Scroll and click
    call_button.scroll_into_view_if_needed()
    page.wait_for_timeout(random.uniform(300, 800))

    try:
        call_button.click(timeout=5000)
    except Exception:
        call_button.click(force=True)

    page.wait_for_timeout(random.uniform(2500, 4000))

    # 4) Try API again after click
    data = _call_api_directly(page, listing_id, ad_url)
    if has_valid_phone(data):
        return data

    # 5) If API returned name but no valid phone, treat as failure
    if isinstance(data, dict) and data.get("name") is not None:
        print(f"  [EMPTY-PHONE] {ad_url} | Name: {data.get('name')} (no valid number)")
        return None

    return None


def fetch_contact_info(page, ad_url: str, max_retries: int = 3) -> dict | None:
    match = re.search(r"ID(\d+)\.html", ad_url or "")
    if not match:
        print(f"  [PARSE-FAIL] {ad_url}")
        return None
    listing_id = match.group(1)

    for attempt in range(1, max_retries + 1):
        try:
            data = _try_fetch_once(page, ad_url, listing_id)
        except Exception as e:
            err_str = str(e)
            if "Timeout" in err_str or "net::" in err_str or "ERR_" in err_str:
                if attempt < max_retries:
                    wait = random.uniform(2, 5)
                    print(f"    [RETRY] network error (attempt {attempt}): {e}")
                    page.wait_for_timeout(wait * 1000)
                    continue
            print(f"  [NETWORK-FAIL] {ad_url} | {e}")
            return None

        if isinstance(data, dict) and data.get("_no_phone"):
            print(f"  [NO-BUTTON] {ad_url}")
            return None

        if isinstance(data, dict) and has_valid_phone(data):
            print(f"  [SUCCESS] {ad_url}")
            return data

        if data is not None:
            print(f"  [EMPTY-API] {ad_url}")
            return None

        if attempt < max_retries:
            wait = random.uniform(2, 5)
            print(f"    [RETRY] empty response (attempt {attempt}), waiting {wait:.1f}s...")
            page.wait_for_timeout(wait * 1000)

    print(f"  [FAILED] {ad_url}")
    return None