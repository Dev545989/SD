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

EMPTY_CONTACT_INFO = {
    "name": None,
    "mobile": None,
    "whatsapp": None,
    "proxyMobile": None,
    "mobileNumbers": [],
    "roles": [],
}


def build_ad_url(record: dict) -> str | None:
    """
    Ad pages look like:
    https://www.dubizzle.sa/en/ad/{slug}-ID{externalID}.html

    Builds it from the record's own `externalID` + `slug` fields. Verify these column
    names match your raw CSV -- adjust if the ES source uses different keys
    (e.g. externalID instead of id).
    """
    ad_id = record.get("externalID")
    slug = record.get("slug")
    if not ad_id or not slug:
        return None
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", clean_text(slug)).strip("-").lower()
    return AD_URL_TEMPLATE.format(slug=slug or "ad", externalID=ad_id)


def _try_fetch_once(page, ad_url: str, listing_id: str):
    """
    One attempt with proper Playwright interaction.
    Uses expect_response to catch the API call triggered by the button click.
    """
    captured_data = None

    def handle_response(response):
        nonlocal captured_data
        if f"/api/listing/{listing_id}/contactInfo/" in response.url:
            try:
                captured_data = response.json()
            except Exception:
                pass

    page.on("response", handle_response)

    try:
        # 1. Load page fully (networkidle = all JS loaded)
        page.goto(ad_url, wait_until="networkidle", timeout=60000)
        tracker.log_request(source="scraping_phone_num")
        
        # 2. Wait a bit for any lazy JS
        page.wait_for_timeout(random.uniform(1000, 2000))

        # 3. Check if API already fired on page load (pre-loaded)
        if captured_data and captured_data.get("name"):
            return captured_data

        # 4. Find the button
        call_button = None
        for selector in CONTACT_BUTTON_SELECTORS:
            loc = page.locator(selector).first
            try:
                if loc.is_visible(timeout=3000):
                    call_button = loc
                    break
            except Exception:
                continue

        if call_button is None:
            return captured_data  # may be None

        # 5. Scroll and click normally (NOT force=True)
        call_button.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        # 6. Click and WAIT for the API response explicitly
        try:
            with page.expect_response(
                lambda r: f"/api/listing/{listing_id}/contactInfo/" in r.url,
                timeout=8000
            ) as resp_info:
                call_button.click()  # ← normal click, no force
            
            response = resp_info.value
            captured_data = response.json()
            
        except Exception:
            # Fallback: click force + wait
            call_button.click(force=True)
            page.wait_for_timeout(3000)
            # captured_data may have been set by handle_response

        return captured_data

    except Exception as e:
        print(f"    [WARN] contact fetch attempt failed: {e}")
        return None
    finally:
        page.remove_listener("response", handle_response)


def fetch_contact_info(page, ad_url: str, max_retries: int = 2) -> dict:
    match = re.search(r"ID(\d+)\.html", ad_url or "")
    if not match:
        return dict(EMPTY_CONTACT_INFO)
    listing_id = match.group(1)

    for attempt in range(1, max_retries + 1):
        data = _try_fetch_once(page, ad_url, listing_id)
        
        # Success: got data with at least a name
        if data and data.get("name") is not None:
            return data
        
        if attempt < max_retries:
            wait = random.uniform(2, 4)
            print(f"    [RETRY] contact info empty (attempt {attempt}), waiting {wait:.1f}s...")
            page.wait_for_timeout(wait * 1000)

    print(f"    [FAIL] contact info empty after {max_retries} attempts for {ad_url}")
    return dict(EMPTY_CONTACT_INFO)