import sys
sys.stdout.reconfigure(encoding='utf-8')

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from bs4 import BeautifulSoup

import os
import re
import time
import requests

# --------------------------------------------------
# Config
# --------------------------------------------------
MIN_DISCOUNT_PCT    = 50     # Today's Deals page: minimum discount % (slider)
SEARCH_MIN_DISCOUNT = 49     # Targeted searches: minimum discount %
MAX_PRICE           = None   # Price cap in dollars; set e.g. 200 to filter out expensive items
MIN_STARS           = 4.0    # Minimum star rating for search results; set None to skip
MIN_REVIEWS         = 50     # Minimum review count for search results; set None to skip
PUSHOVER_SOUND      = "cashregister"
AMAZON_DEALS_URL    = "https://www.amazon.com/deals"
SCROLL_PAUSE        = 2.0

# Specific products to watch.
# "keywords" filters out sponsored/unrelated results — a result only qualifies
# if its title contains at least one of these words (case-insensitive).
# Leave SEARCH_QUERIES = [] to skip targeted searches entirely.
SEARCH_QUERIES = [
    {"query": "pellet smoker",          "keywords": ["pellet", "smoker", "grill", "bbq"]},
    {"query": "red light therapy mask", "keywords": ["red light", "therapy", "mask", "led", "photon"]},
]

# Today's Deals blocklist — skip any deal whose title contains one of these words.
# Add anything you're tired of seeing (case-insensitive).
DEALS_BLOCKLIST = [
    "headphone", "earbud", "earphone", "headset",
    "air purifier", "purifier",
    "vacuum", "robot vacuum", "robot mop",
]

# --------------------------------------------------
# Pushover config
# --------------------------------------------------
PUSHOVER_USER  = "unra8yddj48h5utjouiq7iy3ryow4v"
PUSHOVER_TOKEN = "a9t7s9oud1tmv4bjgd1fe87awkora2"

def send_pushover(title, message, priority=0):
    try:
        resp = requests.post("https://api.pushover.net/1/messages.json", data={
            "token":    PUSHOVER_TOKEN,
            "user":     PUSHOVER_USER,
            "title":    title,
            "message":  message,
            "priority": priority,
            "sound":    PUSHOVER_SOUND,
        }, timeout=10)
        if resp.status_code == 200:
            print("📲 Pushover notification sent.")
        else:
            print(f"⚠️  Pushover responded with {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"⚠️  Could not send Pushover notification: {e}")

# --------------------------------------------------
# HTTP session for search mode — no browser needed
# --------------------------------------------------
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

def search_amazon(query):
    """Fetch Amazon search results via HTTP and parse with BeautifulSoup."""
    url = f"https://www.amazon.com/s?k={query.replace(' ', '+')}"
    session = requests.Session()
    session.get("https://www.amazon.com", headers=SEARCH_HEADERS, timeout=15)
    time.sleep(2)
    resp = session.get(url, headers=SEARCH_HEADERS, timeout=15)
    print(f"   Search HTTP status: {resp.status_code}")
    return BeautifulSoup(resp.text, "html.parser")

# --------------------------------------------------
# Chrome setup — used for Today's Deals slider mode
# --------------------------------------------------
driver = None

def launch_driver():
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    if os.environ.get("CI"):
        opts.add_argument("--headless=new")
    return webdriver.Chrome(options=opts)

def screenshot(name):
    path = f"screenshot_{name}.png"
    driver.save_screenshot(path)
    print(f"📸 Screenshot saved: {path}")

def parse_price(text):
    try:
        cleaned = re.sub(r'[^\d.]', '', text)
        return float(cleaned) if cleaned else None
    except:
        return None

def parse_discount(text):
    try:
        match = re.search(r'(\d+)', text)
        return int(match.group(1)) if match else None
    except:
        return None

def get_amazon_price_context(asin, drv):
    """
    Visit the Amazon product page and scrape all price context Amazon shows:
    - Typical price (what Amazon considers normal)
    - List price (manufacturer's suggested price)
    - "Lowest price in X days" badge if present
    - CamelCamelCamel link for the full chart
    Returns a dict of whatever was found.
    """
    result = {
        "typical_price": None,
        "list_price":    None,
        "lowest_badge":  None,
        "camel_url":     f"https://camelcamelcamel.com/product/{asin}",
    }

    try:
        print(f"   📊 Visiting product page for price history...")
        drv.get(f"https://www.amazon.com/dp/{asin}")
        time.sleep(4)

        def get_offscreen(selector):
            els = drv.find_elements(By.CSS_SELECTOR, selector)
            for el in els:
                val = parse_price(drv.execute_script("return arguments[0].textContent;", el))
                if val and val > 0:
                    return val
            return None

        result["typical_price"] = get_offscreen("#tp_price_block_total_price_ww .a-offscreen")
        if result["typical_price"] is None:
            result["typical_price"] = get_offscreen(".basisPrice .a-offscreen")
        result["list_price"] = get_offscreen(".basisPrice .a-offscreen") or get_offscreen("#listPrice")

        for xpath in [
            "//*[contains(text(),'Lowest price in')]",
            "//*[contains(text(),'lowest price in')]",
            "//*[contains(text(),'Lowest in')]",
        ]:
            els = drv.find_elements(By.XPATH, xpath)
            for el in els:
                txt = el.text.strip()
                if txt and len(txt) < 80:
                    result["lowest_badge"] = txt
                    break
            if result["lowest_badge"]:
                break

        found = {k: v for k, v in result.items() if v and k != "camel_url"}
        print(f"      Found: {found}")

    except Exception as e:
        print(f"   ⚠️  Product page scrape failed: {e}")

    return result

def keyboard_slide(slider_el, target_val):
    from selenium.webdriver.common.keys import Keys
    current_val = int(slider_el.get_attribute("value") or 0)
    steps = target_val - current_val
    key = Keys.ARROW_RIGHT if steps > 0 else Keys.ARROW_LEFT
    driver.execute_script("arguments[0].focus();", slider_el)
    time.sleep(0.2)
    for _ in range(abs(steps)):
        slider_el.send_keys(key)
    time.sleep(1.5)
    return int(slider_el.get_attribute("value") or 0)

def set_discount_filter(target_min_pct):
    try:
        print(f"🎚️  Setting discount filter to {target_min_pct}%+...")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        sliders = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='range']"))
        )
        min_discount = next(
            (s for s in sliders if (s.get_attribute("aria-label") or "").lower() in ("minimum discount", "min discount")),
            None
        )
        if min_discount is None:
            print("⚠️  Could not find 'Minimum discount' slider — skipping.")
            return False

        slider_min = int(min_discount.get_attribute("min") or 0)
        slider_max = int(min_discount.get_attribute("max") or 100)
        print(f"   Discount slider: min={slider_min}, max={slider_max}")

        target_val = round((target_min_pct / 100) * slider_max)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", min_discount)
        time.sleep(0.3)

        new_val = keyboard_slide(min_discount, target_val)
        actual_pct = round((new_val / slider_max) * 100)
        print(f"   ✅ Discount slider set to internal value {new_val} (~{actual_pct}% off minimum)")
        return True

    except Exception as e:
        print(f"⚠️  Discount filter failed: {e}")
        return False

def set_price_filter(max_price_dollars):
    try:
        print(f"💲 Setting price filter to $0–${max_price_dollars}...")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

        from selenium.webdriver.common.keys import Keys
        sliders = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='range']"))
        )
        max_price_slider = next(
            (s for s in sliders if (s.get_attribute("aria-label") or "").lower() in ("maximum price", "max price")),
            None
        )
        if max_price_slider is None:
            print("⚠️  Could not find 'Maximum Price' slider — skipping.")
            return False

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", max_price_slider)
        time.sleep(0.3)
        driver.execute_script("arguments[0].focus();", max_price_slider)
        time.sleep(0.2)

        max_steps = int(max_price_slider.get_attribute("max") or 193)
        for _ in range(max_steps):
            val_text = max_price_slider.get_attribute("aria-valuetext") or ""
            price_val = parse_price(val_text)
            if price_val is not None and price_val <= max_price_dollars:
                break
            max_price_slider.send_keys(Keys.ARROW_LEFT)
            time.sleep(0.05)

        final_text = max_price_slider.get_attribute("aria-valuetext") or "?"
        print(f"   ✅ Price slider max set to: {final_text}")
        return True

    except Exception as e:
        print(f"⚠️  Price filter failed: {e}")
        return False

# --------------------------------------------------
# Scraping — Today's Deals page
# --------------------------------------------------
def scrape_deals_page(seen_titles):
    """Scrape Amazon Today's Deals at MIN_DISCOUNT_PCT%+ off using Selenium."""
    deals = []

    print("\n🌐 Loading Amazon Today's Deals page...")
    driver.get(AMAZON_DEALS_URL)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "[class*='ProductCard-module__card_'], [class*='DealCard']"
            ))
        )
        print("✅ Deal cards detected.")
    except:
        print("⚠️  Deal card wait timed out — proceeding anyway...")
        time.sleep(5)
    screenshot("01_initial_load")

    set_discount_filter(MIN_DISCOUNT_PCT)
    screenshot("02_after_discount_filter")

    if MAX_PRICE is not None:
        set_price_filter(MAX_PRICE)
        screenshot("03_after_price_filter")

    print("⏳ Waiting for filtered results...")
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "[class*='ProductCard-module__card_'], [class*='ProductCard-module__cardContainingLink_']"
            ))
        )
        print("   ✅ Cards detected after filter.")
    except:
        print("   ⚠️  Card wait timed out — giving extra 5s...")
        time.sleep(5)

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)
    print("📜 Scrolling to load all results...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(15):
        driver.execute_script("window.scrollBy(0, 800);")
        time.sleep(SCROLL_PAUSE)
        new_height = driver.execute_script("return document.body.scrollHeight")
        print(f"   Scroll {i+1}: {last_height} → {new_height}")
        if new_height == last_height and i > 3:
            print("   ↳ No new content, stopping scroll.")
            break
        last_height = new_height
    screenshot("04_after_scroll")

    all_cards = []
    for sel in ["[class*='ProductCard-module__card_']", "[class*='ProductCard-module__cardContainingLink_']"]:
        found = driver.find_elements(By.CSS_SELECTOR, sel)
        print(f"   Selector '{sel[:55]}': {len(found)} found")
        if found and not all_cards:
            all_cards = found

    print(f"\n🔍 Scraping {len(all_cards)} deal cards...")
    for card in all_cards:
        try:
            card_text = card.text.strip()

            discount_pct = None
            for b in card.find_elements(By.CSS_SELECTOR,
                    ".a-size-mini, [class*='filledRoundedBadgeLabel']"):
                txt = b.text.strip()
                if "%" in txt:
                    discount_pct = parse_discount(txt)
                    if discount_pct:
                        break
            if discount_pct is None:
                price_matches = re.findall(r'\$[\d,]+\.?\d*', card_text)
                prices = sorted(set(filter(None, [parse_price(p) for p in price_matches])))
                if len(prices) >= 2:
                    discount_pct = round((1 - prices[0] / prices[-1]) * 100)
            if discount_pct is None or discount_pct < MIN_DISCOUNT_PCT:
                continue

            title = ""
            for el in card.find_elements(By.CSS_SELECTOR, "[class*='ProductCard-module__title_']"):
                txt = (driver.execute_script("return arguments[0].textContent;", el) or "").strip()
                if txt and "%" not in txt and len(txt) > 5:
                    title = txt
                    break
            if not title:
                lines = [l.strip() for l in card_text.splitlines() if len(l.strip()) > 10]
                text_lines = [l for l in lines if not re.match(r'^[\$\d\.\,\%\-\s]+$', l)
                              and "% off" not in l.lower() and "time deal" not in l.lower()]
                title = max(text_lines, key=len) if text_lines else (lines[0] if lines else "Unknown")

            title = title[:100]
            key = title[:30].lower().strip()
            if not title or key in seen_titles:
                continue

            # Blocklist — skip categories you never want notified about
            title_lower = title.lower()
            if any(blocked in title_lower for blocked in DEALS_BLOCKLIST):
                print(f"   🚫 Blocked: {title[:70]}")
                continue

            seen_titles.add(key)

            current_price = original_price = None
            pay = card.find_elements(By.CSS_SELECTOR, "[class*='ProductCard-module__priceToPay_']")
            if pay:
                os_els = pay[0].find_elements(By.CSS_SELECTOR, ".a-offscreen")
                if os_els:
                    current_price = parse_price(driver.execute_script("return arguments[0].textContent;", os_els[0]))
            orig = card.find_elements(By.CSS_SELECTOR, ".a-text-price")
            if orig:
                os_els = orig[0].find_elements(By.CSS_SELECTOR, ".a-offscreen")
                if os_els:
                    original_price = parse_price(driver.execute_script("return arguments[0].textContent;", os_els[0]))

            link = AMAZON_DEALS_URL
            try:
                a = card.find_element(By.CSS_SELECTOR,
                    "[class*='ProductCard-module__cardContainingLink_'], a[href*='/dp/']")
                raw = a.get_attribute("href") or ""
                m = re.search(r'/dp/([A-Z0-9]{10})', raw)
                link = f"https://www.amazon.com/dp/{m.group(1)}" if m else raw or AMAZON_DEALS_URL
            except:
                pass

            deal = {
                "title":          title,
                "discount_pct":   discount_pct,
                "current_price":  f"${current_price:.2f}" if current_price else "N/A",
                "original_price": f"${original_price:.2f}" if original_price else "N/A",
                "link":           link,
                "source":         "Today's Deals",
            }
            deals.append(deal)
            print(f"   🔥 {discount_pct}% off — {title[:70]}")

        except:
            continue

    print(f"\n✅ Today's Deals: {len(deals)} qualifying deals found")
    return deals

# --------------------------------------------------
# Scraping — Targeted search queries
# --------------------------------------------------
def scrape_search_results(query_config, soup, seen_titles):
    """
    Scrape Amazon search results for one query.
    Keyword filter blocks sponsored/unrelated products from slipping through.
    """
    deals = []
    query    = query_config["query"]
    keywords = [kw.lower() for kw in query_config.get("keywords", [])]

    cards = soup.find_all("div", attrs={"data-component-type": "s-search-result"})
    print(f"\n   [{query}] Found {len(cards)} search result items")

    for card in cards:
        try:
            asin = card.get("data-asin", "")

            # Skip sponsored listings
            if card.find(string=re.compile(r'^Sponsored$', re.I)) or \
               card.select_one(".s-sponsored-label-info-icon, [data-component-type='sp-sponsored-result']"):
                print(f"   ⛔ Skipped (sponsored): {asin}")
                continue

            title_el = card.select_one("h2 .a-text-normal, h2 a span, h2 span")
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            # Keyword filter — skip anything that doesn't match the search intent
            if keywords and not any(kw in title.lower() for kw in keywords):
                print(f"   ⛔ Skipped (keyword mismatch): {title[:55]}")
                continue

            raw_prices = []
            for o in card.select(".a-offscreen"):
                txt = re.sub(r'^(Typical|List|Was|Save|Reg)[\.\:]?\s*', '', o.get_text(strip=True), flags=re.IGNORECASE)
                val = parse_price(txt)
                if val and val > 0:
                    raw_prices.append(val)

            current_price  = min(raw_prices) if raw_prices else None
            original_price = max(raw_prices) if len(raw_prices) > 1 else None

            discount_pct = None
            if current_price and original_price and original_price > current_price:
                discount_pct = round((1 - current_price / original_price) * 100)
            if discount_pct is None:
                for el in card.find_all(string=re.compile(r'\d+% off', re.I)):
                    d = parse_discount(el)
                    if d:
                        discount_pct = d
                        break

            if discount_pct is None or discount_pct < SEARCH_MIN_DISCOUNT:
                continue
            if MAX_PRICE is not None and current_price and current_price > MAX_PRICE:
                continue

            stars = None
            stars_el = card.find("a", attrs={"aria-label": re.compile(r'out of 5 stars', re.I)})
            if stars_el:
                m = re.search(r'([\d\.]+)\s+out of 5', stars_el.get("aria-label", ""))
                if m:
                    stars = float(m.group(1))
            if MIN_STARS is not None and (stars is None or stars < MIN_STARS):
                print(f"   ⛔ Skipped (rating {stars}/5): {title[:50]}")
                continue

            review_count = None
            count_el = card.find("a", attrs={"aria-label": re.compile(r'ratings', re.I)})
            if count_el:
                m = re.search(r'([\d,]+)', count_el.get("aria-label", ""))
                if m:
                    review_count = int(m.group(1).replace(",", ""))
            if MIN_REVIEWS is not None and (review_count is None or review_count < MIN_REVIEWS):
                print(f"   ⛔ Skipped ({review_count} reviews): {title[:50]}")
                continue

            title = title[:100]
            key = title[:30].lower().strip()
            if key in seen_titles:
                continue
            seen_titles.add(key)

            link = f"https://www.amazon.com/dp/{asin}" if asin else AMAZON_DEALS_URL

            card_typical = None
            typ_el = card.select_one(".a-price.a-text-price .a-offscreen")
            if typ_el:
                card_typical = parse_price(typ_el.get_text(strip=True))

            deal = {
                "title":          title,
                "discount_pct":   discount_pct,
                "current_price":  f"${current_price:.2f}" if current_price else "N/A",
                "original_price": f"${original_price:.2f}" if original_price else "N/A",
                "typical_price":  f"${card_typical:.2f}" if card_typical else None,
                "stars":          f"{stars}⭐" if stars else "N/A",
                "reviews":        f"{review_count:,}" if review_count else "N/A",
                "asin":           asin,
                "link":           link,
                "query":          query,
                "source":         f"Search: {query}",
            }
            deals.append(deal)
            print(f"   🔥 {discount_pct}% off | {stars}⭐ ({review_count:,} reviews) — {title[:55]}")

        except:
            continue

    return deals

# --------------------------------------------------
# Main
# --------------------------------------------------
if __name__ == '__main__':
    qualifying_deals = []

    try:
        driver = launch_driver()
        seen_titles = set()

        # --- Part 1: Today's Deals page (broad best-deal scan) ---
        deals_page_results = scrape_deals_page(seen_titles)
        qualifying_deals.extend(deals_page_results)

        # --- Part 2: Targeted search queries (specific product watching) ---
        if SEARCH_QUERIES:
            print(f"\n🔎 Running {len(SEARCH_QUERIES)} targeted search(es)...")
            for query_config in SEARCH_QUERIES:
                print(f"\n🔎 Searching for: {query_config['query']}")
                soup = search_amazon(query_config["query"])
                search_results = scrape_search_results(query_config, soup, seen_titles)
                qualifying_deals.extend(search_results)
                print(f"   ✅ {len(search_results)} qualifying result(s) for '{query_config['query']}'")
                time.sleep(3)

        # --- Deduplicate, sort, notify ---
        unique_deals = list({d["title"][:30].lower().strip(): d for d in qualifying_deals}.values())
        unique_deals.sort(key=lambda x: x["discount_pct"], reverse=True)

        print(f"\n{'='*50}")
        print(f"✅ Total: {len(unique_deals)} qualifying deals found")
        print(f"{'='*50}")

        if unique_deals:
            for d in unique_deals:
                print(f"  🔥 {d['discount_pct']}% off | {d['current_price']} (was {d['original_price']}) | [{d['source']}] {d['title']}")

            if len(unique_deals) <= 4:
                for deal in unique_deals:
                    asin = deal.get('asin', '')
                    price_ctx = get_amazon_price_context(asin, driver) if asin else {}

                    stars_line = f"⭐ {deal['stars']} ({deal['reviews']} reviews)\n" if deal.get('stars') and deal['stars'] != 'N/A' else ""

                    history_lines = []
                    if price_ctx.get("typical_price"):
                        history_lines.append(f"  Typical price: ${price_ctx['typical_price']:.2f}")
                    if price_ctx.get("list_price") and price_ctx.get("list_price") != price_ctx.get("typical_price"):
                        history_lines.append(f"  List price:    ${price_ctx['list_price']:.2f}")
                    if price_ctx.get("lowest_badge"):
                        history_lines.append(f"  🏆 {price_ctx['lowest_badge']}")
                    history_block = "📊 Price context:\n" + "\n".join(history_lines) + "\n" if history_lines else ""
                    camel_line = f"📉 Full history: {price_ctx.get('camel_url', '')}" if asin else ""

                    msg = (
                        f"{deal['title']}\n\n"
                        f"💰 {deal['current_price']} (was {deal['original_price']})\n"
                        f"🏷️ {deal['discount_pct']}% off\n"
                        f"{stars_line}"
                        f"📌 {deal['source']}\n\n"
                        f"{history_block}\n"
                        f"🛒 {deal['link']}\n"
                        f"{camel_line}"
                    )
                    send_pushover(f"🔥 {deal['discount_pct']}% Off on Amazon!", msg, priority=1)
                    time.sleep(1)
            else:
                lines = [
                    f"• {d['discount_pct']}% off — {d['title'][:45]} ({d['current_price']}) [{d['source']}]"
                    for d in unique_deals[:10]
                ]
                send_pushover(
                    f"🔥 {len(unique_deals)} Amazon Deals Found!",
                    "\n".join(lines),
                    priority=1,
                )
        else:
            print("😴 No qualifying deals found this scan.")
            send_pushover(
                "😴 No Amazon Deals Found",
                f"Scan complete. No deals at {MIN_DISCOUNT_PCT}%+ off on Today's Deals or {SEARCH_MIN_DISCOUNT}%+ off in targeted searches.",
                priority=-1,
            )

    except Exception as e:
        print(f"❌ Error: {e}")
        if driver:
            screenshot("error")
        send_pushover("❌ Amazon Deal Scanner Failed", f"Script error:\n\n{e}", priority=1)
        sys.exit(1)

    finally:
        if driver:
            driver.quit()
        print("\n🏁 Browser closed. Scan complete.")
