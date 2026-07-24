import random
import re
from clean_and_upload import clean_text
from request_tracker import tracker

AD_URL_TEMPLATE = "https://www.dubizzle.sa/en/ad/{slug}-ID{externalID}.html"

CONTACT_BUTTON_SELECTORS = [
    'button:has-text("Show phone number")',
    'button:has-text("Show Phone Number")',
    'button:has-text("Show Number")',
    'button:has-text("Call")',
    '[data-testid*="phone" i]',
    '[data-testid*="show-phone" i]',
    '[data-testid="call-cta-button"]',
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
    https://www.dubizzle.sa/en/ad/{slug}-ID{id}.html

    Builds it from the record's own `id` + `slug` fields. Verify these column
    names match your raw CSV -- adjust if the ES source uses different keys
    (e.g. externalID instead of id).
    """
    ad_id = record.get("externalID")
    slug = record.get("slug")
    if not ad_id or not slug:
        return None
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", clean_text(slug)).strip("-").lower()
    return AD_URL_TEMPLATE.format(slug=slug or "ad", id=ad_id)


def fetch_contact_info(page, ad_url: str) -> dict:
    """
    Opens the ad page in an already-open Playwright page, clicks "Show phone
    number" if present, and captures the /api/listing/{id}/contactInfo/
    response.

    Returns EMPTY_CONTACT_INFO (same shape, fields null/empty) when:
    - the listing id can't be parsed from the URL
    - the button never appears (private/expired/no-contact ads -- this is a
      normal case, not an error)
    - the API response never comes back in time

    Never raises -- a failed contact lookup for one ad must never kill the
    whole cleaning run.
    """
    match = re.search(r"ID(\d+)\.html", ad_url or "")
    if not match:
        return dict(EMPTY_CONTACT_INFO)
    listing_id = match.group(1)

    captured = {"contact_data": None}

    def handle_response(response):
        if f"/api/listing/{listing_id}/contactInfo/" in response.url:
            try:
                captured["contact_data"] = response.json()
            except Exception:
                pass

    page.on("response", handle_response)
    try:
        page.goto(ad_url, wait_until="domcontentloaded", timeout=60000)
        tracker.log_request(source="scraping_phone_num")
        page.wait_for_timeout(random.uniform(1500, 3000))

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
            return dict(EMPTY_CONTACT_INFO)

        call_button.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        call_button.click(force=True)
        page.wait_for_timeout(4000)
    except Exception as e:
        print(f"    [ERROR] contactInfo fetch failed for {ad_url}: {e}")
    finally:
        page.remove_listener("response", handle_response)

    return captured["contact_data"] or dict(EMPTY_CONTACT_INFO)