import json
import re
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

WARMUP_URL = "https://www.dubizzle.sa/en/ad/%D9%84%D9%84%D8%A8%D9%8A%D8%B9-%D9%84%D9%83%D8%B2%D8%B3-es350-%D9%85%D9%88%D8%AF%D9%8A%D9%84-2015-ID110681220.html"

match = re.search(r"ID(\d+)\.html", WARMUP_URL)
if not match:
    raise ValueError("Couldn't extract listing ID from URL")
listing_id = match.group(1)
print(f"Extracted listing ID: {listing_id}")

with Stealth().use_sync(sync_playwright()) as p:
    browser = p.chromium.launch(headless=True, channel="chrome")
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="Asia/Dubai",
    )
    page = context.new_page()

    captured = {"contact_data": None}
    all_api_responses = []

    def handle_response(response):
        url = response.url
        # نسجل كل الـ API responses
        if "/m/api/" in url or "leads" in url or "graphql" in url or "/api/" in url:
            all_api_responses.append((url, response.status))
        
        # نستنى الـ contactInfo API بالتحديد
        if f"/api/listing/{listing_id}/contactInfo/" in url:
            print(f">>> contactInfo response seen: status={response.status}")
            try:
                captured["contact_data"] = response.json()
            except Exception as e:
                print(f">>> Failed to parse contactInfo as JSON: {e}")

    page.on("response", handle_response)

    print("Loading page...")
    page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)

    def safe_content(retries=3, delay=1500):
        for attempt in range(retries):
            try:
                return page.content()
            except Exception:
                if attempt == retries - 1:
                    raise
                page.wait_for_timeout(delay)
        return ""

    if "Pardon Our Interruption" in safe_content():
        browser.close()
        raise Exception("Hit Imperva challenge during warmup")

    print("Looking for Show Phone Number button...")

    # الزرار الحقيقي على الـ desktop
    button_selectors = [
        'button:has-text("Show phone number")',
        'button:has-text("Show Phone Number")',
        'button:has-text("Show Number")',
        'button:has-text("Call")',
        '[data-testid*="phone" i]',
        '[data-testid*="show-phone" i]',
        '[data-testid="call-cta-button"]'
    ]
    call_button = None
    for selector in button_selectors:
        loc = page.locator(selector).first
        try:
            if loc.is_visible(timeout=3000):
                call_button = loc
                print(f"Found button with selector: {selector}")
                break
        except Exception:
            continue

    if call_button is None:
        page.screenshot(path="button_not_found.png", full_page=True)
        browser.close()
        raise Exception(
            "Couldn't find the Show Phone Number button. "
            "Saved button_not_found.png for inspection."
        )

    call_button.scroll_into_view_if_needed()
    page.wait_for_timeout(500)

    print("Clicking Show Phone Number button...")
    call_button.click(force=True)

    # استنى الـ response يوصل
    page.wait_for_timeout(5000)

    # لقطة شاشة بعد الضغط
    page.screenshot(path="after_click.png", full_page=True)
    print("Screenshot saved: after_click.png")

    # اطبعي كل الـ API responses
    print(f"\nAll API-like responses seen ({len(all_api_responses)}):")
    for url, status in all_api_responses:
        print(f"  [{status}] {url}")

    browser.close()

    if captured["contact_data"]:
        print("\n" + "=" * 60)
        print("CONTACT INFO RESULT:")
        print("=" * 60)
        print(json.dumps(captured["contact_data"], ensure_ascii=False, indent=2))
        
        # طباعة رقم التليفون بشكل منفصل
        mobile = captured["contact_data"].get("mobile")
        if mobile:
            print(f"\n>>> PHONE NUMBER: {mobile}")
    else:
        print("\nNo contactInfo response captured. "
              "Try increasing wait time or check if a popup/modal blocked the click.")