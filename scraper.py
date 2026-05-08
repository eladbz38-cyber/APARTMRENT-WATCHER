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

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

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

# ── Yad2 (requests with session cookies) ─────────────────────────────────────
def scrape_yad2():
    listings = []
    try:
        session = requests.Session()
        # Warm up the session — get homepage to pick up cookies
        warm_headers = {
            "User-Agent": CHROME_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        }
        try:
            session.get("https://www.yad2.co.il/realestate/rent", headers=warm_headers, timeout=10)
        except Exception:
            pass

        api_headers = {
            "User-Agent": CHROME_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.yad2.co.il/realestate/rent",
            "Origin": "https://www.yad2.co.il",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }
        url = "https://gw.yad2.co.il/realestate-feed/rent"
        params = {"city": YAD2_CITY, "area": "17", "region": "1"}
        r = session.get(url, params=params, headers=api_headers, timeout=15)
        print(f"  יד2 API status={r.status_code} size={len(r.content)}B")

        if r.status_code == 200 and r.content:
            data = r.json()
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
        else:
            print(f"  יד2 תגובה: {r.text[:150]}")
    except Exception as e:
        print(f"שגיאה ביד2: {e}")
    print(f"יד2: {len(listings)} דירות")
    return listings

# ── Yad2 via Playwright (fallback — intercepts the XHR the page itself makes) ─
def scrape_yad2_playwright(page):
    listings = []
    api_hits = []

    def on_response(response):
        url = response.url
        if "gw.yad2.co.il" in url:
            print(f"    yad2 xhr: {url[:100]} [{response.status}]")
            # Match both old and new feed endpoints
            if "realestate-feed/rent" in url and "/map" not in url:
                try:
                    api_hits.append(response.json())
                except Exception:
                    pass
            elif "feed-search-legacy" in url:
                try:
                    api_hits.append(response.json())
                except Exception:
                    pass

    page.on("response", on_response)
    try:
        page.goto(
            f"https://www.yad2.co.il/realestate/rent?city={YAD2_CITY}",
            timeout=30000,
            wait_until="domcontentloaded",
        )
        time.sleep(random.uniform(5, 7))
        # Dismiss consent/cookie popups
        for sel in ["button:has-text('אישור')", "button:has-text('הסכמה')",
                    "button:has-text('קבל')", "#onetrust-accept-btn-handler"]:
            try:
                page.click(sel, timeout=2000)
            except Exception:
                pass
        time.sleep(2)
        # Scroll to trigger lazy-load
        page.keyboard.press("End")
        time.sleep(3)
    except Exception as e:
        print(f"  יד2 playwright navigation: {e}")
    page.remove_listener("response", on_response)

    print(f"  יד2 playwright: {len(api_hits)} API hits")

    # Fallback 1: use the browser's auth cookies to fetch the listing API
    if not api_hits:
        try:
            print("  יד2: מנסה fetch מהדפדפן...")
            api_data = page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch(
                            'https://gw.yad2.co.il/realestate-feed/rent?city={YAD2_CITY}&area=17&region=1',
                            {{credentials: 'include', headers: {{Accept: 'application/json'}}}}
                        );
                        if (!r.ok) return {{error: r.status + ' ' + r.statusText}};
                        return await r.json();
                    }} catch(e) {{
                        return {{error: String(e)}};
                    }}
                }}
            """)
            if api_data and not api_data.get("error"):
                print("  יד2 browser fetch: הצליח!")
                api_hits.append(api_data)
            else:
                print(f"  יד2 browser fetch: נכשל - {api_data}")
        except Exception as e:
            print(f"  יד2 browser fetch שגיאה: {e}")

    # Fallback 2: parse __NEXT_DATA__ (Yad2 is Next.js — listings may be SSR'd)
    if not api_hits:
        try:
            next_data_str = page.evaluate(
                "() => { const el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null; }"
            )
            if next_data_str:
                nd = json.loads(next_data_str)
                props = nd.get("props", {}).get("pageProps", {})
                print(f"  יד2 __NEXT_DATA__ מפתחות: {list(props.keys())[:10]}")
                items_nd = (
                    props.get("feedItems")
                    or props.get("feed_items")
                    or props.get("listings")
                    or props.get("items")
                    or (props.get("feed") or {}).get("feed_items", [])
                    or []
                )
                print(f"  יד2 __NEXT_DATA__: {len(items_nd)} פריטים")
                if items_nd:
                    api_hits.append({"items": items_nd})
            else:
                print("  יד2: __NEXT_DATA__ לא נמצא")
        except Exception as e:
            print(f"  יד2 __NEXT_DATA__ שגיאה: {e}")

    for data in api_hits:
        # Support both old feed_items and new data formats
        items = (
            data.get("data", {}).get("feed", {}).get("feed_items", [])
            or data.get("data", {}).get("items", [])
            or data.get("items", [])
            or []
        )
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
    return listings

# ── Madlan via Playwright ─────────────────────────────────────────────────────
def scrape_madlan_playwright(page):
    listings = []
    try:
        page.goto(
            "https://www.madlan.co.il/for-rent/nahalat-yehuda-rishon-lezion",
            timeout=30000,
            wait_until="domcontentloaded",
        )
        time.sleep(random.uniform(4, 6))
        title = page.title()
        print(f"  מדלן page title: {title[:60]}")

        next_data_str = page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null; }"
        )
        if next_data_str:
            data = json.loads(next_data_str)
            props = data.get("props", {}).get("pageProps", {})
            print(f"  מדלן pageProps keys: {list(props.keys())[:8]}")
            items = (
                props.get("listings")
                or props.get("items")
                or props.get("searchResults", {}).get("listings", [])
                or []
            )
            for item in items[:30]:
                lid = str(item.get("id", item.get("listingId", "")))
                nb = item.get("neighborhood", {})
                neighborhood = nb.get("name", "") if isinstance(nb, dict) else ""
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
        else:
            print("  מדלן: __NEXT_DATA__ לא נמצא")
    except Exception as e:
        print(f"שגיאה במדלן: {e}")
    print(f"מדלן: {len(listings)} דירות")
    return listings

# ── Facebook login ────────────────────────────────────────────────────────────
def fb_login(page):
    from playwright.sync_api import TimeoutError as PWTimeout
    page.goto("https://www.facebook.com/login/", timeout=30000)
    time.sleep(random.uniform(3, 5))

    # Dismiss cookie / consent banners
    for sel in [
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        "button:has-text('Allow all cookies')",
        "button:has-text('Accept all')",
        "button:has-text('OK')",
        "[aria-label='Allow all cookies']",
    ]:
        try:
            page.click(sel, timeout=2000)
            time.sleep(1)
        except Exception:
            pass

    # Try multiple selectors for the email field
    logged_in = False
    for email_sel in ["#email", "input[name='email']", "input[type='email']"]:
        try:
            page.wait_for_selector(email_sel, timeout=8000)
            page.fill(email_sel, FB_EMAIL)
            time.sleep(random.uniform(0.5, 1.0))
            page.fill("#pass", FB_PASSWORD)
            time.sleep(random.uniform(0.5, 1.0))
            page.click("[name='login']")
            time.sleep(random.uniform(7, 10))
            logged_in = True
            print("  פייסבוק: התחברות בוצעה")
            break
        except Exception:
            continue

    if not logged_in:
        print(f"  פייסבוק: login נכשל, URL={page.url[:80]}")

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
                link_el = post.query_selector(
                    "a[href*='/posts/'], a[href*='story_fbid'], a[href*='/permalink/']"
                )
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

# ── Main Playwright session ───────────────────────────────────────────────────
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
                user_agent=CHROME_UA,
                locale="he-IL",
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()

            yad2_pl = scrape_yad2_playwright(page)
            listings += yad2_pl
            time.sleep(random.uniform(2, 4))

            madlan = scrape_madlan_playwright(page)
            listings += madlan
            time.sleep(random.uniform(2, 4))

            if FB_EMAIL and FB_PASSWORD:
                fb_login(page)
                listings += scrape_fb_marketplace(page)
                time.sleep(random.uniform(2, 4))
                for group_url in FB_GROUPS:
                    listings += scrape_fb_group(page, group_url)
                    time.sleep(random.uniform(2, 4))
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

    # 1. Try Yad2 directly with a requests session (faster, works if not IP-blocked)
    yad2_direct = scrape_yad2()

    # 2. Run Playwright for Yad2 fallback + Madlan + Facebook
    playwright_listings = scrape_all_playwright()

    # Merge — prefer direct Yad2 if it found something
    all_listings = yad2_direct + [l for l in playwright_listings if not l["id"].startswith("yad2_")]
    if not yad2_direct:
        # Include playwright's yad2 results too
        all_listings = playwright_listings

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
