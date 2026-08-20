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
    ad_id = record.get("externalID")
    slug = record.get("slug")
    if not ad_id or not slug:
        return None
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", clean_text(slug)).strip("-").lower()
    return AD_URL_TEMPLATE.format(slug=slug or "ad", externalID=ad_id)


def _try_fetch_once(page, ad_url: str, listing_id: str):
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
        # ← BACK to domcontentloaded (networkidle hangs too often)
        page.goto(ad_url, wait_until="domcontentloaded", timeout=30000)
        tracker.log_request(source="scraping_phone_num")
        page.wait_for_timeout(random.uniform(1500, 2500))

        # Already captured on page load?
        if captured_data is not None:
            return captured_data

        # Find button
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
            # ← NORMAL: private ad, no phone button. Don't retry.
            return {"_no_phone": True}

        call_button.scroll_into_view_if_needed()
        page.wait_for_timeout(300)

        # Click and catch response
        try:
            with page.expect_response(
                lambda r: f"/api/listing/{listing_id}/contactInfo/" in r.url,
                timeout=6000
            ) as resp_info:
                call_button.click(force=True)
            
            response = resp_info.value
            captured_data = response.json()

        except Exception:
            # Fallback: force click + wait
            call_button.click(force=True)
            page.wait_for_timeout(3000)

        return captured_data

    except Exception as e:
        # Only retry on real network errors
        if "Timeout" in str(e) or "net::" in str(e):
            raise
        return None
    finally:
        page.remove_listener("response", handle_response)


def fetch_contact_info(page, ad_url: str, max_retries: int = 2) -> dict:
    match = re.search(r"ID(\d+)\.html", ad_url or "")
    if not match:
        return dict(EMPTY_CONTACT_INFO)
    listing_id = match.group(1)

    for attempt in range(1, max_retries + 1):
        try:
            data = _try_fetch_once(page, ad_url, listing_id)
        except Exception as e:
            # Real network error → retry
            if attempt < max_retries:
                wait = random.uniform(1, 3)
                print(f"    [RETRY] network error (attempt {attempt}): {e}")
                page.wait_for_timeout(wait * 1000)
                continue
            print(f"    [SKIP] network failed after {max_retries} attempts: {ad_url}")
            return dict(EMPTY_CONTACT_INFO)

        # No phone button = normal, don't retry
        if isinstance(data, dict) and data.get("_no_phone"):
            return dict(EMPTY_CONTACT_INFO)

        # Got data (even if mobile is null — that's valid)
        if isinstance(data, dict) and data.get("name") is not None:
            return data

        # API returned empty JSON — no phone available, don't retry
        if data is not None:
            return dict(EMPTY_CONTACT_INFO)

        # data is None = something went wrong, retry if we have attempts left
        if attempt < max_retries:
            wait = random.uniform(1, 3)
            print(f"    [RETRY] empty response (attempt {attempt}), waiting {wait:.1f}s...")
            page.wait_for_timeout(wait * 1000)

    print(f"    [SKIP] No contact info for {ad_url}")
    return dict(EMPTY_CONTACT_INFO)