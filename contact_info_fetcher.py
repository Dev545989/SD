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


def _try_fetch_once(page, ad_url: str, listing_id: str, max_wait_ms: int = 8000):
    """
    One attempt to fetch contact info.
    Uses polling instead of fixed sleep.
    """
    captured = {"contact_data": None, "api_status": None}

    def handle_response(response):
        if f"/api/listing/{listing_id}/contactInfo/" in response.url:
            captured["api_status"] = response.status
            try:
                captured["contact_data"] = response.json()
            except Exception:
                pass

    # Attach listener BEFORE goto (in case API fires on page load)
    page.on("response", handle_response)

    try:
        page.goto(ad_url, wait_until="domcontentloaded", timeout=60000)
        tracker.log_request(source="scraping_phone_num")
        
        # Wait for page JS to settle
        page.wait_for_timeout(random.uniform(1500, 2500))

        # --- Phase 1: Check if API already captured (pre-loaded) ---
        if captured["contact_data"] is not None:
            return captured["contact_data"]

        # --- Phase 2: Find and click the button ---
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
            # No button — maybe private ad or API already gave us nothing
            return captured["contact_data"]  # may be None

        call_button.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        call_button.click(force=True)

        # --- Phase 3: Poll for API response (up to max_wait_ms) ---
        poll_interval = 200  # ms
        polls = int(max_wait_ms / poll_interval)
        for _ in range(polls):
            if captured["contact_data"] is not None:
                break
            page.wait_for_timeout(poll_interval)

        return captured["contact_data"]

    except Exception as e:
        print(f"    [WARN] contact fetch attempt failed: {e}")
        return None
    finally:
        page.remove_listener("response", handle_response)


def fetch_contact_info(page, ad_url: str, max_retries: int = 2) -> dict:
    """
    Fetch contact info with retries.
    Returns EMPTY_CONTACT_INFO only when all retries exhaust.
    """
    match = re.search(r"ID(\d+)\.html", ad_url or "")
    if not match:
        return dict(EMPTY_CONTACT_INFO)
    listing_id = match.group(1)

    for attempt in range(1, max_retries + 1):
        data = _try_fetch_once(page, ad_url, listing_id)
        
        # Success: got data with at least one phone number
        if data and (data.get("mobile") or data.get("mobileNumbers")):
            return data
        
        # Partial success: got JSON but empty phones — still better than null
        if data and data != EMPTY_CONTACT_INFO:
            return data
        
        if attempt < max_retries:
            wait = random.uniform(2, 4)
            print(f"    [RETRY] contact info empty (attempt {attempt}), waiting {wait:.1f}s...")
            page.wait_for_timeout(wait * 1000)

    # All retries failed
    print(f"    [FAIL] contact info empty after {max_retries} attempts for {ad_url}")
    return dict(EMPTY_CONTACT_INFO)