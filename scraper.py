import os
import json
import smtplib
import time
import random
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)
FB_EMAIL = os.environ.get("FB_EMAIL", "")
FB_PASSWORD = os.environ.get("FB_PASSWORD", "")

SEEN_FILE = Path("seen_listings.json")

YAD2_CITY = "7400"  # Rishon LeZion
TARGET_NEIGHBORHOODS = ["נחלת יהודה", "האלה", "הלוחמים"]

FB_GROUPS = [
    "https://www.facebook.com/groups/475698279278222",
    "https://www.facebook.com/groups/1388942934745587",
    "https://www.facebook.com/groups/2243438782566887",
    "https://www.facebook.com/groups/587145908109887",
    "https://www.facebook.com/groups/dirot.rishon",
    "https://www.facebook.com/groups/rishonlezionrent",
    "https://www.facebook.com/groups/rishon.apartments",
    "https://www.facebook.com/groups/nahalyehuda.rent",
    "https://www.facebook.com/groups/rishonlezionrealestate",
    "https://www.facebook.com/groups/dirotlehaskara.rishon",
]

FB_KEYWORDS = ["נחלת יהודה", "האלה", "הלוחמים", "להשכרה", "מושכר", "דירה"]

# ── Seen listings ────────────────────────────────────────────────────────────
def load_seen():
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return set(data) if data else set()
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")

# ── Email ────────────────────────────────────────────────────────────────────
def send_email(listings: list, subject_prefix=""):
    if not listings:
        return
    subject = f"{subject_prefix}🏠 {len(listings)} דירות בנחלת יהודה / האלה - ראשון לציון"
    body_parts = []
    for l in listings:
        body_parts.append(f"""
━━━━━━━━━━━━━━━━━━━━━━
📌 מקור: {l.get('source', '')}
📍 {l.get('neighborhood', 'ראשון לציון')} | {l.get('city', 'ראשון לציון')}
💰 מחיר: {l.get('price', 'לא צוין')} ₪/חודש
🛏  {l.get('rooms', '?')} חדרים | {l.get('size', '?')} מ"ר
📝 {l.get('description', '')[:200]}
🔗 {l.get('url', '')}
📞 {l.get('contact', '')}
🕐 {l.get('date', '')}
""")
    body = "נמצאו דירות הבאות:\n" + "\n".join(body_parts)
    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
    print(f"✅ מייל נשלח עם {len(listings)} דירות")

# ── Yad2 via Playwright (intercept API response) ──────────────────────────
def scrape_yad2_playwright(page):
    listings = []
    api_responses = []

    def on_response(response):
        if "feed-search-legacy/realestate/rent" in response.url:
            try:
                api_responses.append(response.json())
            except Exception:
                pass

    page.on("response", on_response)
    try:
        page.goto(
            f"https://www.yad2.co.il/realestate/rent?city={YAD2_CITY}",
            timeout=30000,
            wait_until="networkidle",
        )
        time.sleep(random.uniform(3, 5))
    except Exception as e:
        print(f"  יד2 navigation: {e}")
    page.remove_listener("response", on_response)

    for data in api_responses:
        items = data.get("data", {}).get("feed", {}).get("feed_items", [])
        for item in items:
            if item.get("type") != "ad":
                continue
            neighborhood = item.get("neighborhood_text", "")
            address = item.get("address_str", "") + " " + item.get("title_1", "")
            if not any(n in neighborhood or n in address for n in TARGET_NEIGHBORHOODS):
                continue
            listings.append({
                "id": f"yad2_{item.get('id', '')}",
                "source": "יד2",
                "city": "ראשון לציון",
                "neighborhood": neighborhood,
                "price": item.get("price", ""),
                "rooms": item.get("rooms", ""),
                "size": item.get("square_meters", ""),
                "description": item.get("title_1", "") + " " + item.get("title_2", ""),
                "url": f"https://www.yad2.co.il/item/{item.get('id', '')}",
                "contact": item.get("contactName", ""),
                "date": item.get("date_added", ""),
            })
    print(f"יד2: {len(listings)} דירות (מתוך {len(api_responses)} תגובות API)")
    return listings

# ── Madlan via Playwright ─────────────────────────────────────────────────
def scrape_madlan_playwright(page):
    listings = []
    try:
        page.goto(
            "https://www.madlan.co.il/for-rent/nahalat-yehuda-rishon-lezion",
            timeout=30000,
            wait_until="domcontentloaded",
        )
        time.sleep(random.uniform(3, 5))
        # Extract __NEXT_DATA__ JSON embedded in the page
        next_data = page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)
        if next_data:
            data = json.loads(next_data)
            props = data.get("props", {}).get("pageProps", {})
            # Try different keys where listings might be
            items = (
                props.get("listings")
                or props.get("items")
                or props.get("searchResults", {}).get("listings", [])
                or []
            )
            for item in items[:30]:
                lid = str(item.get("id", item.get("listingId", "")))
                neighborhood = ""
                nb = item.get("neighborhood", {})
                if isinstance(nb, dict):
                    neighborhood = nb.get("name", "נחלת יהודה")
                listings.append({
                    "id": f"madlan_{lid}",
                    "source": "מדלן",
                    "city": "ראשון לציון",
                    "neighborhood": neighborhood,
                    "price": item.get("price", ""),
                    "rooms": item.get("rooms", ""),
                    "size": item.get("squareMeter", ""),
                    "description": item.get("title", item.get("description", ""))[:200],
                    "url": f"https://www.madlan.co.il/listing/{lid}",
                    "contact": "",
                    "date": item.get("publishedAt", ""),
                })
        print(f"מדלן: {len(listings)} דירות")
    except Exception as e:
        print(f"שגיאה במדלן: {e}")
    return listings

# ── Facebook ──────────────────────────────────────────────────────────────────
def fb_login(page):
    from playwright.sync_api import TimeoutError as PWTimeout
    # Go directly to login page
    page.goto("https://www.facebook.com/login", timeout=30000)
    time.sleep(random.uniform(3, 5))
    # Close cookie banner if present
    for selector in [
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        "button:has-text('Allow all cookies')",
        "button:has-text('Accept all')",
        "button:has-text('OK')",
    ]:
        try:
            page.click(selector, timeout=3000)
            time.sleep(1)
        except Exception:
            pass
    try:
        page.wait_for_selector("#email", timeout=15000)
        page.fill("#email", FB_EMAIL)
        time.sleep(random.uniform(0.5, 1.0))
        page.fill("#pass", FB_PASSWORD)
        time.sleep(random.uniform(0.5, 1.0))
        page.click("[name='login']")
        time.sleep(random.uniform(6, 9))
        print("  פייסבוק: התחברות בוצעה")
    except PWTimeout:
        print("  פייסבוק: דף login לא נמצא, ממשיך...")

def scrape_fb_marketplace(page):
    listings = []
    try:
        search_url = (
            "https://www.facebook.com/marketplace/rishon-lezion/propertyrentals"
            "?radius=3&latitude=31.9642&longitude=34.8086"
        )
        page.goto(search_url, timeout=30000)
        time.sleep(random.uniform(5, 8))
        for _ in range(2):
            page.keyboard.press("End")
            time.sleep(2)
        items = page.query_selector_all('[aria-label="Marketplace item"]')
        if not items:
            items = page.query_selector_all('div[data-testid="marketplace_feed_item"]')
        for item in items[:25]:
            try:
                title_text = item.inner_text()
                link = item.query_selector("a")
                href = link.get_attribute("href") if link else ""
                if href and not href.startswith("http"):
                    href = "https://www.facebook.com" + href
                lid = href.split("/item/")[1].split("/")[0] if "/item/" in href else str(abs(hash(href)))
                listings.append({
                    "id": f"fb_market_{lid}",
                    "source": "Facebook Marketplace",
                    "city": "ראשון לציון",
                    "neighborhood": "",
                    "price": "", "rooms": "", "size": "",
                    "description": title_text[:200],
                    "url": href, "contact": "",
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
            except Exception:
                continue
        print(f"  Marketplace: {len(listings)} פריטים")
    except Exception as e:
        print(f"  שגיאת Marketplace: {e}")
    return listings

def scrape_fb_group(page, group_url):
    listings = []
    try:
        page.goto(group_url, timeout=30000)
        time.sleep(random.uniform(4, 6))
        for _ in range(3):
            page.keyboard.press("End")
            time.sleep(random.uniform(1, 2))
        posts = page.query_selector_all('[role="article"]')
        for post in posts[:15]:
            try:
                text = post.inner_text()
                if not any(kw in text for kw in FB_KEYWORDS):
                    continue
                if "להשכרה" not in text and "דירה" not in text and "חדר" not in text:
                    continue
                link_el = post.query_selector("a[href*='/posts/'], a[href*='story_fbid'], a[href*='/permalink/']")
                href = ""
                if link_el:
                    href = link_el.get_attribute("href") or ""
                    if not href.startswith("http"):
                        href = "https://www.facebook.com" + href
                lid = str(abs(hash(text[:50])))
                listings.append({
                    "id": f"fb_grp_{lid}",
                    "source": "קבוצת פייסבוק",
                    "city": "ראשון לציון",
                    "neighborhood": next((n for n in TARGET_NEIGHBORHOODS if n in text), ""),
                    "price": "", "rooms": "", "size": "",
                    "description": text[:300],
                    "url": href or group_url,
                    "contact": "",
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
            except Exception:
                continue
        print(f"  קבוצה {group_url.split('/')[-1]}: {len(listings)} פוסטים")
    except Exception as e:
        print(f"  שגיאת קבוצה: {e}")
    return listings

# ── Main Playwright session (Yad2 + Madlan + Facebook) ───────────────────
def scrape_all_playwright():
    listings = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--lang=he-IL",
                ]
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="he-IL",
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            # Yad2 (intercept API)
            yad2_listings = scrape_yad2_playwright(page)
            listings += yad2_listings
            time.sleep(random.uniform(2, 4))

            # Madlan
            madlan_listings = scrape_madlan_playwright(page)
            listings += madlan_listings
            time.sleep(random.uniform(2, 4))

            # Facebook
            if FB_EMAIL and FB_PASSWORD:
                fb_login(page)
                listings += scrape_fb_marketplace(page)
                time.sleep(random.uniform(2, 4))
                for group_url in FB_GROUPS:
                    listings += scrape_fb_group(page, group_url)
                    time.sleep(random.uniform(2, 4))
                fb_total = len(listings) - len(yad2_listings) - len(madlan_listings)
                print(f"פייסבוק סה\"כ: {fb_total}")
            else:
                print("פייסבוק: אין פרטי התחברות")

            browser.close()
    except Exception as e:
        print(f"שגיאה כללית ב-Playwright: {e}")
    return listings

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n🔍 סריקה התחילה: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    seen = load_seen()
    is_first_run = len(seen) == 0

    all_listings = scrape_all_playwright()

    print(f"סה\"כ נמצאו: {len(all_listings)} דירות מכל המקורות")

    new_listings = [l for l in all_listings if l["id"] not in seen]
    print(f"✨ {len(new_listings)} דירות חדשות")

    if new_listings:
        send_email(new_listings)
        for l in new_listings:
            seen.add(l["id"])
        save_seen(seen)
    elif is_first_run and all_listings:
        send_email(all_listings, subject_prefix="[ריצה ראשונה] ")
        for l in all_listings:
            seen.add(l["id"])
        save_seen(seen)
    elif is_first_run:
        print("ריצה ראשונה אך לא נמצאו דירות — לא נשלח מייל")
    else:
        print("אין דירות חדשות מאז הסריקה האחרונה")

    print("✅ סריקה הסתיימה")

if __name__ == "__main__":
    main()
