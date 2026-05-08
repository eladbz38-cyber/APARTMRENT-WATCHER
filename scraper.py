import os
import json
import smtplib
import time
import random
import requests
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

# Rishon LeZion neighborhoods to watch (Nahalat Yehuda + HaEla area)
YAD2_CITY = "7400"  # Rishon LeZion
TARGET_NEIGHBORHOODS = ["נחלת יהודה", "האלה", "הלוחמים"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "he-IL,he;q=0.9",
}

# ── Seen listings store ──────────────────────────────────────────────────────
def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")

# ── Email ────────────────────────────────────────────────────────────────────
def send_email(listings: list):
    if not listings:
        return
    subject = f"🏠 {len(listings)} דירה/ות חדשה/ות בנחלת יהודה / האלה!"
    body_parts = []
    for l in listings:
        body_parts.append(f"""
━━━━━━━━━━━━━━━━━━━━━━
📍 {l.get('neighborhood', '')} | {l.get('city', 'ראשון לציון')}
💰 {l.get('price', 'לא צוין')} ₪/חודש
🛏 {l.get('rooms', '?')} חדרים | {l.get('size', '?')} מ"ר
📝 {l.get('description', '')}
🔗 {l.get('url', '')}
📞 {l.get('contact', '')}
🕐 {l.get('date', '')}
""")
    body = "דירות חדשות שפורסמו:\n" + "\n".join(body_parts)

    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
    print(f"✅ Sent email with {len(listings)} listings")

# ── Yad2 scraper ─────────────────────────────────────────────────────────────
def scrape_yad2():
    listings = []
    try:
        url = "https://gw.yad2.co.il/feed-search-legacy/realestate/rent"
        params = {
            "city": YAD2_CITY,
            "priceOnly": "1",
            "forceLdLoad": "true",
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", {}).get("feed", {}).get("feed_items", [])
        for item in items:
            if item.get("type") == "ad":
                neighborhood = item.get("neighborhood_text", "")
                if not any(n in neighborhood for n in TARGET_NEIGHBORHOODS):
                    continue
                listing = {
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
                }
                listings.append(listing)
        print(f"יד2: נמצאו {len(listings)} דירות רלוונטיות")
    except Exception as e:
        print(f"שגיאה ביד2: {e}")
    return listings

# ── Madlan scraper ────────────────────────────────────────────────────────────
def scrape_madlan():
    listings = []
    try:
        url = "https://www.madlan.co.il/api2/listings"
        params = {
            "query": "נחלת יהודה ראשון לציון",
            "dealType": "rent",
            "pageSize": "40",
        }
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        for item in data.get("listings", []):
            neighborhood = item.get("neighborhood", {}).get("name", "")
            if not any(n in neighborhood for n in TARGET_NEIGHBORHOODS):
                city = item.get("city", {}).get("name", "")
                if "ראשון לציון" not in city:
                    continue
            listing = {
                "id": f"madlan_{item.get('id', '')}",
                "source": "מדלן",
                "city": item.get("city", {}).get("name", "ראשון לציון"),
                "neighborhood": neighborhood,
                "price": item.get("price", ""),
                "rooms": item.get("rooms", ""),
                "size": item.get("squareMeter", ""),
                "description": item.get("title", ""),
                "url": f"https://www.madlan.co.il/listing/{item.get('id', '')}",
                "contact": "",
                "date": item.get("publishedAt", ""),
            }
            listings.append(listing)
        print(f"מדלן: נמצאו {len(listings)} דירות רלוונטיות")
    except Exception as e:
        print(f"שגיאה במדלן: {e}")
    return listings

# ── Facebook Marketplace scraper ──────────────────────────────────────────────
def scrape_facebook():
    if not FB_EMAIL or not FB_PASSWORD:
        print("פייסבוק: לא הוגדרו פרטי התחברות, מדלג")
        return []
    listings = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="he-IL",
            )
            page = ctx.new_page()

            # Login
            page.goto("https://www.facebook.com/login", timeout=30000)
            time.sleep(random.uniform(2, 4))
            page.fill("#email", FB_EMAIL)
            page.fill("#pass", FB_PASSWORD)
            page.click("[name='login']")
            time.sleep(random.uniform(4, 7))

            # Search Marketplace
            search_url = (
                "https://www.facebook.com/marketplace/rishon-lezion/propertyrentals"
                "?radius=3&latitude=31.9642&longitude=34.8086&topicId=propertyrentals"
            )
            page.goto(search_url, timeout=30000)
            time.sleep(random.uniform(4, 8))

            items = page.query_selector_all('[aria-label="Marketplace item"]')
            for item in items[:20]:
                try:
                    title = item.query_selector("span")
                    title_text = title.inner_text() if title else ""
                    link = item.query_selector("a")
                    href = link.get_attribute("href") if link else ""
                    if href and not href.startswith("http"):
                        href = "https://www.facebook.com" + href
                    listing_id = href.split("/item/")[1].split("/")[0] if "/item/" in href else href
                    listing = {
                        "id": f"fb_{listing_id}",
                        "source": "Facebook Marketplace",
                        "city": "ראשון לציון",
                        "neighborhood": "",
                        "price": "",
                        "rooms": "",
                        "size": "",
                        "description": title_text,
                        "url": href,
                        "contact": "",
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    listings.append(listing)
                except Exception:
                    continue
            browser.close()
        print(f"פייסבוק: נמצאו {len(listings)} דירות")
    except Exception as e:
        print(f"שגיאה בפייסבוק: {e}")
    return listings

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n🔍 סריקה התחילה: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    seen = load_seen()

    all_listings = []
    all_listings += scrape_yad2()
    time.sleep(random.uniform(2, 5))
    all_listings += scrape_madlan()
    time.sleep(random.uniform(2, 5))
    all_listings += scrape_facebook()

    new_listings = [l for l in all_listings if l["id"] not in seen]
    print(f"✨ {len(new_listings)} דירות חדשות (מתוך {len(all_listings)} סה\"כ)")

    if new_listings:
        send_email(new_listings)
        for l in new_listings:
            seen.add(l["id"])
        save_seen(seen)

    print("✅ סריקה הסתיימה")

if __name__ == "__main__":
    main()
